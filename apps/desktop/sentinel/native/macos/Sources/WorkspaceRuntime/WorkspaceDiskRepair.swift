import Containerization
import Foundation
import Darwin

// Retain an unfinished checker so a retry must confirm it powered off before
// mounting the same disk again. Runtime ownership also prevents another helper
// from repairing the disk concurrently.
@MainActor private var unfinishedCheckers: [String: LinuxContainer] = [:]

@MainActor
func stopWorkspaceDiskChecker(_ id: String) async throws {
    guard let previous = unfinishedCheckers[id] else { return }
    guard let machine = try await previous.withVirtualMachineInstance({ $0 as? VZVirtualMachineInstance }) else {
        throw RuntimeError("Cannot confirm the disk checker is powered off. Restart Sentinel before retrying.")
    }
    try await powerOffWorkspace(machine)
    unfinishedCheckers.removeValue(forKey: id)
}

/// Called only after macOS confirms the workspace VM has powered off.
/// Repair is conservative (-p), keeps a pre-repair backup, and never touches the
/// project directory. The existing serialized lifecycle owns the manager.
@MainActor
func repairWorkspaceDisk(_ id: String, root: URL, image: String, manager: inout ContainerManager) async throws -> String {
    try await stopWorkspaceDiskChecker(id)
    let disk = root.appendingPathComponent("store/containers/\(id)/rootfs.ext4")
    let values = try disk.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
    guard values.isRegularFile == true, values.isSymbolicLink != true else { throw RuntimeError("Workspace disk is missing or unsafe") }
    let directory = root.appendingPathComponent("recovery/\(id)/\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    let backup = directory.appendingPathComponent("rootfs.ext4")
    try await Task.detached {
        if clonefile(disk.path, backup.path, 0) != 0 { try FileManager.default.copyItem(at: disk, to: backup) }
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: backup.path)
    }.value
    let report = directory.appendingPathComponent("result.txt")
    try "Backup saved. Offline repair pending.\n".write(to: report, atomically: true, encoding: .utf8)
    // Unique registration prevents a previous failed check from being removed
    // while its VM could still own resources.
    let repairID = UUID().uuidString + "-repair"
    let output = Capture()
    let repair = try await manager.create(repairID, reference: image, rootfsSizeInBytes: 2 * 1024 * 1024 * 1024) { @Sendable config in
        config.cpus = 1
        config.memoryInBytes = 512 * 1024 * 1024
        config.mounts.append(.share(source: disk.deletingLastPathComponent().path, destination: "/disk"))
        config.process.arguments = ["sh", "-ec", """
        apk add --no-cache e2fsprogs
        result=0
        e2fsck -f -p /disk/rootfs.ext4 || result=$?
        sync
        [ "$result" -le 1 ] || exit "$result"
        """]
        config.process.stdout = output
        config.process.stderr = output
    }
    unfinishedCheckers[id] = repair
    var machine: VZVirtualMachineInstance?
    do {
        try await repair.create()
        machine = try await repair.withVirtualMachineInstance { $0 as? VZVirtualMachineInstance }
        try await runtimeDeadline(300) {
            try await repair.start()
            try Task.checkCancellation()
            let result = try await repair.wait(timeoutInSeconds: 240)
            try Task.checkCancellation()
            guard result.exitCode == 0 else { throw RuntimeError("Filesystem check needs manual attention") }
            try await repair.stop()
        }
        unfinishedCheckers.removeValue(forKey: id)
        try manager.delete(repairID)
        try output.text.write(to: report, atomically: true, encoding: .utf8)
        return backup.path
    } catch {
        // Never restart the workspace if we cannot confirm the checker stopped.
        if let machine {
            do {
                try await powerOffWorkspace(machine)
                unfinishedCheckers.removeValue(forKey: id)
            } catch {
                throw RuntimeError("Disk checker did not stop. The workspace remains blocked. Backup: \(backup.path). Restart this machine before retrying recovery.")
            }
        }
        try? (output.text + "\n" + String(describing: error)).write(to: report, atomically: true, encoding: .utf8)
        throw RuntimeError("Disk repair did not complete. Workspace is stopped. Backup and report: \(directory.path)")
    }
}
