import Containerization
import ContainerizationError
import Darwin
import Foundation
import Synchronization

struct Request: Decodable, Sendable {
    let id: String
    let action: String
    var workspace: String?
    var project: String?
    var distribution: String?
    // Private raw-start build input; not a worker catalog or workspace setting.
    var image_reference: String?
    var desktop: WorkspaceDesktop?
    var arguments: [String]?
    var timeout: Int64?
    var process: String?
    var data: String?
    var terminal: Bool?
    var cols: UInt16?
    var rows: UInt16?
    var width: Int?
    var height: Int?
    var cpus: Int?
    var memory_gib: UInt64?
    var disk_gib: UInt64?
    var grow_disk: Bool?
    var approved_workspaces: [String]?
    var port: Int?
    var spec: WorkerWorkspaceSpec?
    var revision: Int?
    var steps: [WorkerSetupStep]?
    var confirmed: Bool?
}

struct Response: Encodable, Sendable {
    // Breaking worker contract revision; independent of release/build identity.
    var protocol_version: Int = 2
    var id: String?
    var event: String?
    var error: String?
    var message: String?
    var stdout: String?
    var stderr: String?
    var exitCode: Int32?
    var truncated: Bool?
    var states: [String: String]?
    var process: String?
    var data: String?
    var socket: String?
    var distributions: [String]?
    var capabilities: [String]?
    var errors: [String: String]?
    var backup: String?
    var worker_id: String?
    var workspaces: [String: WorkerWorkspaceRecord]?
}

struct RuntimeError: Error, CustomStringConvertible {
    let description: String
    init(_ message: String) { description = message }
}

// Guest output must never share the JSON control stream. Bound captures even for
// a command that produces unlimited output; interactive terminals use a separate
// transport when the backend adopts this helper.
final class Capture: Writer {
    struct State { var data = Data(); var truncated = false }
    private let state = Mutex(State())
    func write(_ data: Data) {
        state.withLock {
            let remaining = max(0, 4 * 1024 * 1024 - $0.data.count)
            $0.data.append(data.prefix(remaining))
            $0.truncated = $0.truncated || data.count > remaining
        }
    }
    func close() {}
    var text: String { state.withLock { String(decoding: $0.data, as: UTF8.self) } }
    var truncated: Bool { state.withLock { $0.truncated } }
}

final class Input: ReaderStream {
    let pair = AsyncStream<Data>.makeStream()
    func stream() -> AsyncStream<Data> { pair.stream }
}

final class RuntimeLog: Writer {
    let workspace: String
    init(_ workspace: String) { self.workspace = workspace }
    func write(_ data: Data) {
        let text = String(decoding: data, as: UTF8.self)
        FileHandle.standardError.write(Data("[workspace \(workspace)] \(text)".utf8))
    }
    func close() {}
}

final class Output: Writer {
    let id: String
    init(_ id: String) { self.id = id }
    func write(_ data: Data) throws {
        // Keep each protocol frame bounded for the desktop and HTTP bridge.
        for offset in stride(from: 0, to: data.count, by: 32768) {
            try WorkspaceRuntime.emit(Response(event: "output", process: id,
                data: data.subdata(in: offset..<min(offset + 32768, data.count)).base64EncodedString()))
        }
    }
    func close() {}
}

actor Processes {
    var entries: [String: (String, LinuxProcess, Input)] = [:]
    func add(_ id: String, workspace: String, process: LinuxProcess, input: Input) {
        entries[id] = (workspace, process, input)
    }
    func get(_ id: String, workspace: String) throws -> (LinuxProcess, Input) {
        guard let entry = entries[id], entry.0 == workspace else { throw RuntimeError("Process is no longer running") }
        return (entry.1, entry.2)
    }
    func remove(_ id: String) { entries.removeValue(forKey: id)?.2.pair.continuation.finish() }
    func forget(_ workspace: String) {
        for (id, entry) in entries where entry.0 == workspace {
            entry.2.pair.continuation.finish()
            entries.removeValue(forKey: id)
        }
    }
    func stop(_ workspace: String) async {
        for (id, entry) in entries where entry.0 == workspace {
            entry.2.pair.continuation.finish()
            try? await entry.1.kill(.kill)
            entries.removeValue(forKey: id)
        }
    }
}

@main
struct WorkspaceRuntime {
    // Native init gets 20 seconds to exit; SDK cleanup then waits up to another
    // 5 seconds for init and releases guest/host resources. Bound the whole
    // operation below the caller's 60-second deadline, not the ordinary
    // 15-second guest RPC deadline. SDK teardown has no overall timeout.
    static let shutdownTimeoutSeconds: Double = 45

    static func stop(_ container: LinuxContainer, signal: Signal?) async throws {
        if let signal {
            do {
                try await container.kill(signal)
                _ = try await container.wait(timeoutInSeconds: 20)
            } catch {
                try? FileHandle.standardError.write(contentsOf: Data(
                    "Native Linux shutdown did not complete gracefully: \(error)\n".utf8))
            }
        }
        // Always release sockets, mounts and the VM, even after init exited.
        try await container.stop()
    }

    static let health = WorkspaceHealth()
    static func answerStatus(_ line: String) -> Bool {
        guard let request = try? JSONDecoder().decode(Request.self, from: Data(line.utf8)), request.action == "status" else { return false }
        try? emit(health.status(request.id))
        return true
    }

    static let outputLock = Mutex(0)
    static let remoteControl = Mutex<RemoteControl?>(nil)

    static func emit(_ response: Response) throws {
        var bytes = try JSONEncoder().encode(response)
        bytes.append(10)
        try outputLock.withLock { _ in
            if let remote = remoteControl.withLock({ $0 }) { remote.emit(response, bytes: bytes) }
            else { try FileHandle.standardOutput.write(contentsOf: bytes) }
        }
    }

    static func main() async {
        signal(SIGPIPE, SIG_IGN)
        do {
            if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--update-session" {
                try RuntimeUpdate.serve(CommandLine.arguments[2])
                return
            }
            if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--ping" {
                try RemoteControl.ping(path: CommandLine.arguments[2])
                return
            }
            if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--relay" {
                try RemoteControl.relay(path: CommandLine.arguments[2])
                return
            }
            try await serve()
        }
        catch is RemoteSocketUnavailable {
            try? emit(Response(event: "unavailable", error: "Remote runtime socket is not available yet"))
            exit(1)
        }
        catch {
            // Retain diagnostics even when initialization fails before a relay
            // connects. Remote control events alone have no durable consumer.
            try? FileHandle.standardError.write(contentsOf: Data("Workspace runtime failed: \(String(describing: error))\n".utf8))
            try? emit(Response(event: "fatal", error: String(describing: error)))
            exit(1)
        }
    }

    @MainActor static func serve() async throws {
        var args = CommandLine.arguments
        let inherited = args.count > 1 && args[1] == "--owned-service"
        let remote = inherited || (args.count > 1 && args[1] == "--service")
        if remote { args.remove(at: 1) }
        guard args.count == 4 else {
            throw RuntimeError("Expected: sentinel-workspace-runtime STATE_ROOT KERNEL INIT_IMAGE")
        }
        let root = URL(fileURLWithPath: args[1], isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        // Locks only Sentinel's private store, never any global engine state.
        // Ordinary starts cooperate with remote updates. An updater hands its
        // already-held owner lock to the replacement without an unlocked gap.
        let updateGate = remote && !inherited
            ? try RuntimeUpdate.lock(root.appendingPathComponent("update.lock").path, LOCK_SH) : -1
        defer { if updateGate >= 0 { close(updateGate) } }
        let lock = inherited ? RuntimeUpdate.inheritedOwner
            : open(root.appendingPathComponent("owner.lock").path, O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0o600)
        guard lock >= 0, flock(lock, LOCK_EX | LOCK_NB) == 0 else {
            throw RuntimeError("Sentinel's workspace runtime is already open")
        }
        defer { close(lock) }
        if inherited { _ = fcntl(lock, F_SETFD, FD_CLOEXEC) }
        if updateGate >= 0 { flock(updateGate, LOCK_UN) }
        if remote {
            let control = try RemoteControl(path: root.appendingPathComponent("control.sock").path, answer: answerStatus)
            remoteControl.withLock { $0 = control }
        }
        try emit(Response(event: "preparing"))
        let store = try ImageStore(path: root.appendingPathComponent("store"))
        for (reference, message) in [(args[3], "Preparing workspace boot image…")] {
            try emit(Response(event: "preparing", message: message))
            do { _ = try await store.get(reference: reference) }
            catch let error as ContainerizationError where error.code == .notFound {
                _ = try await store.pull(reference: reference, platform: .init(arch: "arm64", os: "linux"))
            }
        }
        var manager = try await ContainerManager(
            kernel: Kernel(path: URL(fileURLWithPath: args[2]), platform: .linuxArm,
                           commandline: .init(kernelArgs: Kernel.CommandLine.kernelDefaults + ["security=apparmor", "apparmor=1"])),
            initfsReference: args[3], imageStore: store,
            network: try VmnetNetwork()
        )
        var containers: [String: LinuxContainer] = [:]
        var shutdownSignals: [String: Signal] = [:]
        var machines: [String: VZVirtualMachineInstance] = [:]
        for id in (try? FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent("store/containers").path)) ?? [] where UUID(uuidString: id) != nil { health.set(id, "stopped") }
        try health.restore(root: root)
        let processes = Processes()
        var displays: [String: HostDisplay] = [:]
        var forwards: [String: [Int: GuestPortForward]] = [:]
        let graphicsResources = URL(fileURLWithPath: args[0]).resolvingSymlinksInPath().deletingLastPathComponent().appendingPathComponent("graphics")
        let worker = remote ? try WorkerWorkspaces(root: root) : nil
        let lifecycle = RuntimeRequestQueue()
        let managerGate = RuntimeMutationGate()
        @MainActor func perform(_ request: Request) async throws -> Response {
            guard let id = request.workspace, UUID(uuidString: id) != nil else { throw RuntimeError("A workspace UUID is required") }
            var mutatesManager = ["start", "recover", "delete"].contains(request.action)
            if mutatesManager { await managerGate.acquire() }
            defer { if mutatesManager { managerGate.release() } }
            try Task.checkCancellation()
            try health.check(id, action: request.action)
            if request.action == "exec" { return try await executeGuest(request, container: containers[id]) }
            switch request.action {
            case "display_start":
                guard let container = containers[id] else { throw RuntimeError("Start the workspace before its desktop") }
                if let display = displays[id] {
                    guard display.active else { throw RuntimeError("The virtual GPU stopped. Restart this workspace to reconnect its desktop.") }
                    return Response(id: request.id, socket: display.videoPath)
                }
                let path = try GuestPortForward.socketPath(root: root, workspace: id, port: 5901)
                let display = try HostDisplay(resources: graphicsResources, videoPath: path,
                                              controlPath: HostDisplay.controlPath(root: root, workspace: id))
                displays[id] = display
                try await health.guest(id) { try await display.start(container: container, width: request.width ?? 1280, height: request.height ?? 720) }
                return Response(id: request.id, socket: display.videoPath)
            case "browser_graphics_install":
                guard let container = containers[id] else { throw RuntimeError("Start the workspace before installing browser graphics") }
                guard request.distribution == "ubuntu" else { throw RuntimeError("The confined graphics provider is only required on Ubuntu") }
                try await installGuestBrowserGraphics(id, container: container, resources: graphicsResources)
                return Response(id: request.id)
            case "graphics_install":
                guard let container = containers[id] else { throw RuntimeError("Start the workspace before installing graphics") }
                try await installGuestGraphics(id, container: container, resources: graphicsResources, distribution: request.distribution ?? "alpine")
                if let desktop = request.desktop, desktop != .none {
                    let result = try await executeGuest(Request(id: UUID().uuidString, action: "exec", workspace: id,
                        arguments: ["python3", "/opt/sentinel/desktop/provision-desktop.py", desktop.rawValue], timeout: 900), container: container)
                    guard result.exitCode == 0 else { throw RuntimeError(result.stderr ?? "Desktop provisioning failed") }
                }
                return Response(id: request.id)
            case "recover":
                try health.beginRecovery(id)
                do {
                    if let machine = machines[id] { try await powerOffWorkspace(machine) }
                    else if containers[id] != nil { throw RuntimeError("Cannot confirm the VM is powered off") }
                    if let display = displays.removeValue(forKey: id) { await display.stop(guestAvailable: false) }
                    for forward in forwards.removeValue(forKey: id)?.values ?? Dictionary<Int, GuestPortForward>().values {
                        try await runtimeDeadline(15) { await forward.stop() }
                    }
                    await processes.forget(id)
                    containers.removeValue(forKey: id)
                    machines.removeValue(forKey: id)
                    try manager.releaseNetwork(id)
                    let maintenanceImages = try WorkspaceImages(directory: graphicsResources
                        .deletingLastPathComponent().appendingPathComponent("workspace-images"))
                    let maintenanceImage = try maintenanceImages.entry(for: .alpine)
                    try await maintenanceImages.prepare(maintenanceImage, store: store)
                    let backup = try await repairWorkspaceDisk(id, root: root, image: maintenanceImage.reference, manager: &manager)
                    try health.completeRecovery(id)
                    return Response(id: request.id, event: "recovered", backup: backup)
                } catch {
                    health.fault(id, "Recovery did not complete: \(error)")
                    throw error
                }
            case "port_forward":
                guard let container = containers[id], let port = request.port, (1...65535).contains(port) else {
                    throw RuntimeError("A running workspace and valid guest TCP port are required")
                }
                if port == 5901, let display = displays[id] {
                    guard display.active else { throw RuntimeError("Virtual display is not running") }
                    return Response(id: request.id, socket: display.videoPath)
                }
                if forwards[id]?[port] == nil {
                    let path = try GuestPortForward.socketPath(root: root, workspace: id, port: port)
                    let forward = try GuestPortForward(path: path, container: container, port: port)
                    forwards[id, default: [:]][port] = forward
                }
                return Response(id: request.id, socket: forwards[id]?[port]?.path)
            case "start":
                if containers[id] == nil {
                    guard let cpus = request.cpus, (1...32).contains(cpus),
                          let memory = request.memory_gib, (1...64).contains(memory),
                          let diskSize = request.disk_gib, (1...1024).contains(diskSize) else {
                        throw RuntimeError("Workspace CPU, memory and disk sizes are required")
                    }
                    guard let project = request.project, project.hasPrefix("/") else {
                        throw RuntimeError("An absolute project folder is required")
                    }
                    var directory: ObjCBool = false
                    guard FileManager.default.fileExists(atPath: project, isDirectory: &directory), directory.boolValue else {
                        throw RuntimeError("The selected project folder does not exist")
                    }
                    let resolvedProject = URL(fileURLWithPath: project).resolvingSymlinksInPath().path
                    let resolvedRoot = root.resolvingSymlinksInPath().path
                    guard resolvedProject != "/", resolvedProject != resolvedRoot,
                          !resolvedRoot.hasPrefix(resolvedProject + "/"),
                          !resolvedProject.hasPrefix(resolvedRoot + "/") else {
                        throw RuntimeError("The project folder must not contain Sentinel's private runtime storage")
                    }
                    guard let distribution = WorkspaceDistribution(rawValue: request.distribution ?? "alpine") else {
                        throw RuntimeError("Unknown workspace distribution")
                    }
                    let disk = root.appendingPathComponent("store/containers/\(id)/rootfs.ext4")
                    let bootFile = disk.deletingLastPathComponent().appendingPathComponent("boot.json")
                    let boot: WorkspaceImages.DiskBoot?
                    let imageReference: String
                    let bootCommand: [String]
                    if let reference = request.image_reference {
                        try WorkspaceDistribution.validateImageReference(reference)
                        imageReference = reference
                        bootCommand = distribution.buildCommand
                        boot = nil
                    } else {
                        let catalog = try WorkspaceImages(directory: URL(fileURLWithPath: args[0])
                            .resolvingSymlinksInPath().deletingLastPathComponent().appendingPathComponent("workspace-images"))
                        let stored = FileManager.default.fileExists(atPath: disk.path)
                            ? try WorkspaceImages.requireDiskBoot(at: bootFile, distribution: distribution)
                            : WorkspaceImages.DiskBoot(distribution: distribution.rawValue, image: try catalog.entry(for: distribution))
                        // An existing native disk keeps its recorded image/boot identity.
                        // Missing old image blobs must not reinterpret or rebuild that disk.
                        if stored.image == (try catalog.entry(for: distribution)) {
                            try await catalog.prepare(stored.image, store: store)
                        }
                        imageReference = stored.image.reference
                        bootCommand = stored.image.entrypoint
                        boot = stored
                    }
                    do { _ = try await store.get(reference: imageReference) }
                    catch let error as ContainerizationError where error.code == .notFound {
                        guard request.image_reference != nil else {
                            throw RuntimeError("This workspace's recorded Linux image is missing from local storage. Reinstall the workspace to rebuild its system disk; its existing disk has not been changed.")
                        }
                        _ = try await store.pull(reference: imageReference, platform: .init(arch: "arm64", os: "linux"))
                    }
                    let displayControl = try HostDisplay.controlPath(root: root, workspace: id)
                    let configure: @Sendable (inout LinuxContainer.Configuration) throws -> Void = { config in
                        config.cpus = cpus
                        config.memoryInBytes = memory * 1024 * 1024 * 1024
                        // tmpfs allocates on demand; allow up to 1 GiB of shared
                        // memory without exceeding half of a smaller VM's RAM.
                        let sharedMemoryBytes = min(config.memoryInBytes / 2, 1024 * 1024 * 1024)
                        config.mounts.removeAll { $0.destination == "/dev/shm" }
                        config.mounts.append(.any(type: "tmpfs", source: "tmpfs", destination: "/dev/shm",
                                                  options: ["nosuid", "noexec", "nodev", "mode=1777", "size=\(sharedMemoryBytes)"]))
                        config.mounts.append(.share(source: project, destination: project))
                        // Runtime sockets and PID files must not survive a VM
                        // reboot. Package installs and Docker data remain on disk.
                        config.mounts.append(.any(type: "tmpfs", source: "tmpfs", destination: "/run",
                                                  options: ["nosuid", "nodev", "mode=755"]))
                        // The workspace has its own mount namespace. Native init
                        // does not mount securityfs in container environments;
                        // expose the VM's LSM interfaces for AppArmor and snapd.
                        // Explicit build images use the pinned bootstrap kernel,
                        // which need not provide workspace confinement features.
                        if request.image_reference == nil {
                            config.mounts.append(.any(type: "securityfs", source: "securityfs",
                                                      destination: "/sys/kernel/security",
                                                      options: ["nosuid", "nodev", "noexec"]))
                        }
                        config.sockets.append(.init(source: URL(fileURLWithPath: displayControl),
                            destination: URL(fileURLWithPath: "/run/sentinel-desktop/display.sock"),
                            permissions: .init(rawValue: 0o600), direction: .into))
                        config.process.workingDirectory = project
                        config.process.arguments = bootCommand
                        config.process.environmentVariables += guestGPUEnvironment
                        config.process.stdout = RuntimeLog(id)
                        config.process.stderr = RuntimeLog(id)
                        config.process.capabilities = .allCapabilities
                        config.maskedPaths = []
                        config.readonlyPaths = []
                    }
                    let distroFile = disk.deletingLastPathComponent().appendingPathComponent("distribution")
                    let container: LinuxContainer
                    if FileManager.default.fileExists(atPath: disk.path) {
                        let saved = FileManager.default.fileExists(atPath: distroFile.path)
                            ? try String(contentsOf: distroFile, encoding: .utf8) : "alpine"
                        guard saved == distribution.rawValue else { throw RuntimeError("Create a new workspace to change its distribution") }
                        let requestedBytes = diskSize * 1024 * 1024 * 1024
                        let currentBytes = try disk.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                        guard requestedBytes >= currentBytes else { throw RuntimeError("Workspace disks cannot be shrunk") }
                        if requestedBytes > currentBytes {
                            guard request.grow_disk == true else { throw RuntimeError("Disk expansion must be requested explicitly") }
                            let handle = try FileHandle(forWritingTo: disk)
                            defer { try? handle.close() }
                            try handle.truncate(atOffset: requestedBytes)
                            try handle.synchronize()
                        }
                        if request.grow_disk == true {
                            // The SDK formats sparse_super2 EXT4 disks, which require offline growth.
                            // Only this trusted maintenance VM sees the stopped workspace's disk file.
                            let maintenanceID = id + "-resize"
                            let maintenanceImages = try WorkspaceImages(directory: URL(fileURLWithPath: args[0])
                                .resolvingSymlinksInPath().deletingLastPathComponent().appendingPathComponent("workspace-images"))
                            let maintenanceImage = try maintenanceImages.entry(for: .alpine)
                            try await maintenanceImages.prepare(maintenanceImage, store: store)
                            let maintenancePath = root.appendingPathComponent("store/containers/\(maintenanceID)")
                            if FileManager.default.fileExists(atPath: maintenancePath.path) { try manager.delete(maintenanceID) }
                            let output = Capture()
                            let maintenance = try await manager.create(maintenanceID, reference: maintenanceImage.reference, rootfsSizeInBytes: 2 * 1024 * 1024 * 1024) { @Sendable config in
                                config.cpus = 1
                                config.memoryInBytes = 512 * 1024 * 1024
                                config.mounts.append(.share(source: disk.deletingLastPathComponent().path, destination: "/disk"))
                                config.process.arguments = ["sh", "-ec", """
                                apk add --no-cache e2fsprogs-extra
                                result=0
                                e2fsck -f -p /disk/rootfs.ext4 || result=$?
                                [ "$result" -le 1 ] || exit "$result"
                                resize2fs /disk/rootfs.ext4
                                sync
                                """]
                                config.process.stdout = output
                                config.process.stderr = output
                            }
                            do {
                                try await maintenance.create()
                                try await maintenance.start()
                                let status = try await maintenance.wait(timeoutInSeconds: 300)
                                try await maintenance.stop()
                                try manager.delete(maintenanceID)
                                guard status.exitCode == 0 else { throw RuntimeError("Disk expansion failed: \(output.text)") }
                            } catch {
                                try? await maintenance.stop()
                                try? manager.delete(maintenanceID)
                                throw error
                            }
                        }
                        container = try await manager.create(
                            id, image: store.get(reference: imageReference),
                            rootfs: .block(format: "ext4", source: disk.path, destination: "/", options: []),
                            configuration: configure
                        )
                    } else {
                        // A failed initial unpack may leave an empty registration.
                        let registration = disk.deletingLastPathComponent()
                        if FileManager.default.fileExists(atPath: registration.path) {
                            try FileManager.default.removeItem(at: registration)
                        }
                        container = try await manager.create(id, reference: imageReference, rootfsSizeInBytes: diskSize * 1024 * 1024 * 1024, configuration: configure)
                    }
                    try distribution.rawValue.write(to: distroFile, atomically: true, encoding: .utf8)
                    if let boot { try JSONEncoder().encode(boot).write(to: bootFile, options: .atomic) }
                    // The SDK borrow is over. Boot/readiness waits must not hold
                    // other workspaces' network/disk mutations behind this VM.
                    managerGate.release()
                    mutatesManager = false
                    do {
                        try await container.create()
                        machines[id] = try await container.withVirtualMachineInstance { $0 as? VZVirtualMachineInstance }
                        try await container.start()
                        if boot != nil {
                            let ready = try await executeGuest(Request(id: UUID().uuidString, action: "exec", workspace: id,
                                arguments: ["timeout", "25", "sh", "-ec", """
                                until [ -S /run/dbus/system_bus_socket ] && [ -S /run/udev/control ] &&
                                      timeout 2 loginctl list-seats --no-legend >/dev/null 2>&1; do
                                    sleep 0.2
                                done
                                """], timeout: 30), container: container)
                            guard ready.exitCode == 0 else {
                                throw RuntimeError("Native Linux services did not become ready: \(ready.stderr ?? ready.stdout ?? "check workspace boot logs")")
                            }
                        }
                        containers[id] = container
                        shutdownSignals[id] = boot == nil ? nil : distribution.shutdownSignal
                        health.set(id, "running")
                    } catch {
                        try? await container.stop()
                        await managerGate.acquire()
                        defer { managerGate.release() }
                        try? manager.releaseNetwork(id)
                        throw error
                    }
                }
                return Response(id: request.id, event: "started")
            case "delete":
                // Destruction must not depend on a healthy guest. Confirm all disk
                // owners are off before deleting storage, even after failed repair.
                try await stopWorkspaceDiskChecker(id)
                if let machine = machines[id] { try await powerOffWorkspace(machine) }
                else if containers[id] != nil { throw RuntimeError("Cannot confirm the VM is powered off. Restart Sentinel before removing this workspace.") }
                if let display = displays.removeValue(forKey: id) { await display.stop(guestAvailable: false) }
                for forward in forwards.removeValue(forKey: id)?.values ?? Dictionary<Int, GuestPortForward>().values {
                    try await runtimeDeadline(15) { await forward.stop() }
                }
                await processes.forget(id)
                machines.removeValue(forKey: id)
                containers.removeValue(forKey: id)
                shutdownSignals.removeValue(forKey: id)
                for ownedID in [id + "-resize", id] {
                    if FileManager.default.fileExists(atPath: root.appendingPathComponent("store/containers/\(ownedID)").path) {
                        try manager.delete(ownedID)
                    }
                }
                let backups = root.appendingPathComponent("recovery/\(id)")
                if FileManager.default.fileExists(atPath: backups.path) { try FileManager.default.removeItem(at: backups) }
                try health.remove(id)
                return Response(id: request.id, event: "deleted")
            case "stop":
                if let display = displays.removeValue(forKey: id) { try await health.guest(id) { await display.stop() } }
                for forward in forwards.removeValue(forKey: id)?.values ?? Dictionary<Int, GuestPortForward>().values { try await health.guest(id) { await forward.stop() } }
                try await health.guest(id) { await processes.stop(id) }
                if let container = containers[id] {
                    try await health.guest(id, seconds: shutdownTimeoutSeconds) { try await stop(container, signal: shutdownSignals[id]) }
                    health.set(id, "stopped")
                    machines.removeValue(forKey: id)
                    containers.removeValue(forKey: id)
                    shutdownSignals.removeValue(forKey: id)
                }
                await managerGate.acquire()
                defer { managerGate.release() }
                try manager.releaseNetwork(id)
                return Response(id: request.id, event: "stopped")
            default:
                throw RuntimeError("Unknown runtime action")
            }
        }
        worker?.attach(perform)
        try emit(Response(event: "ready"))

        // EOF is the ownership boundary: closing the parent pipe stops only the
        // VMs created by this process, preserving their disks for the next launch.
        let lines: AsyncStream<String>
        if let control = remoteControl.withLock({ $0 }) { lines = control.requests }
        else {
            lines = AsyncStream { continuation in
                Task.detached {
                    while let line = readLine() { if !answerStatus(line) { continuation.yield(line) } }
                    continuation.finish()
                }
            }
        }
        for await line in lines {
            var requestID: String?
            do {
                let request = try JSONDecoder().decode(Request.self, from: Data(line.utf8))
                requestID = request.id
                if request.action == "shutdown" { break }
                if request.action == "maintenance" {
                    await lifecycle.drain()
                    try worker?.requireIdle()
                    let approved = Set(request.approved_workspaces ?? [])
                    guard Set(containers.keys).isSubset(of: approved) else {
                        throw RuntimeError("Running workspaces changed. Review and approve the update again.")
                    }
                    let stopped = Dictionary(uniqueKeysWithValues: containers.keys.map { ($0, "stopped") })
                    // Drained queues and the paused reader prevent a start racing this check.
                    // Acknowledge only after every VM has stopped successfully.
                    for id in Array(containers.keys) {
                        if let display = displays.removeValue(forKey: id) { try await health.guest(id) { await display.stop() } }
                        for forward in forwards.removeValue(forKey: id)?.values ?? Dictionary<Int, GuestPortForward>().values { try await health.guest(id) { await forward.stop() } }
                        try await health.guest(id) { await processes.stop(id) }
                        if let container = containers[id] { try await health.guest(id, seconds: shutdownTimeoutSeconds) { try await stop(container, signal: shutdownSignals[id]) } }
                        containers.removeValue(forKey: id)
                        shutdownSignals.removeValue(forKey: id)
                        machines.removeValue(forKey: id)
                        health.set(id, "stopped")
                        try manager.releaseNetwork(id)
                    }
                    try emit(Response(id: request.id, event: "maintenance", states: stopped))
                    break
                }
                if request.action == "status" {
                    try emit(health.status(request.id))
                    continue
                }
                if let worker {
                    switch request.action {
                    case "workspaces": try emit(worker.inventory(request.id)); continue
                    case "workspace_configure": try emit(worker.configure(request)); continue
                    case "workspace_resume": try emit(worker.resume(request)); continue
                    default: break
                    }
                }
                guard let id = request.workspace, UUID(uuidString: id) != nil else {
                    throw RuntimeError("A workspace UUID is required")
                }
                try health.check(id, action: request.action)
                if ["process_start", "process_input", "process_resize", "process_stop", "exec"].contains(request.action) {
                    try health.reserve(id)
                    let container = containers[id]
                    let generation = health.generation(id)
                    Task {
                        defer { health.release(id) }
                        await WorkspaceHealth.$requestGeneration.withValue(generation) {
                            await guestRequest(request, container: container, processes: processes)
                        }
                    }
                    continue
                }
                lifecycle.submit(workspace: id, action: request.action) {
                    do {
                        if let worker {
                            let reply: Response
                            switch request.action {
                            case "workspace_start", "workspace_reinstall": reply = try worker.start(request)
                            case "workspace_stop": reply = try await worker.stop(request)
                            case "workspace_delete": reply = try await worker.stop(request, remove: true)
                            case "workspace_recover": reply = try worker.recover(request)
                            case "display_start", "port_forward": reply = try await perform(request)
                            default: throw RuntimeError("Unsupported worker request; update your Sentinel client")
                            }
                            try emit(reply)
                        } else { try emit(try await perform(request)) }
                    } catch {
                        try? emit(Response(id: request.id, error: String(describing: error)))
                    }
                }
            } catch {
                try emit(Response(id: requestID, error: String(describing: error)))
            }
        }
        await lifecycle.drain()
        await worker?.finishJobs()
        for display in displays.values { await display.stop() }
        for ports in forwards.values { for forward in ports.values { await forward.stop() } }
        for id in containers.keys { try await health.guest(id) { await processes.stop(id) } }
        await withTaskGroup(of: String?.self) { group in
            for (id, container) in containers {
                let signal = shutdownSignals[id]
                group.addTask {
                    do { try await stop(container, signal: signal); return nil }
                    catch { return String(describing: error) }
                }
            }
            for await error in group {
                if let error { try? emit(Response(event: "shutdown_error", error: error)) }
            }
        }
        for id in containers.keys { try manager.releaseNetwork(id) }
    }
}
