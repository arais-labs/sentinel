// Disposable native-init qualification. This executable is not bundled with Sentinel.
// Imports a real OCI layout and uses the same Containerization VM/mount primitives.
import Containerization
import Foundation
import Synchronization

struct ProbeError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}

final class ProbeOutput: Writer {
    private let bytes = Mutex(Data())
    let live: Bool
    init(live: Bool = false) { self.live = live }
    func write(_ data: Data) {
        if live { FileHandle.standardError.write(data) }
        bytes.withLock { buffer in buffer.append(data.prefix(max(0, 4 * 1024 * 1024 - buffer.count))) }
    }
    func close() {}
    var text: String { bytes.withLock { String(decoding: $0, as: UTF8.self) } }
}

struct ProbeRequest: Decodable {
    let arguments: [String]
    var timeout: Int64?
}

@main struct WorkspaceBootProbe {
    @MainActor static func main() async throws {
        let args = CommandLine.arguments
        guard args.count == 5 else {
            throw ProbeError("Usage: workspace-boot-probe NEW_STORE KERNEL INIT_IMAGE OCI_LAYOUT")
        }
        let root = URL(fileURLWithPath: args[1], isDirectory: true)
        guard !FileManager.default.fileExists(atPath: root.path) else {
            throw ProbeError("Probe requires a new private store, never an existing workspace store")
        }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        let store = try ImageStore(path: root.appendingPathComponent("store"))
        let images = try await store.load(from: URL(fileURLWithPath: args[4]))
        guard images.count == 1 else { throw ProbeError("Expected exactly one native-init image") }
        let image = images[0]
        let imageConfig = try await image.config(for: .init(arch: "arm64", os: "linux"))
        guard let entrypoint = imageConfig.config?.entrypoint,
              entrypoint == ["/sbin/init"] || entrypoint == ["/sbin/openrc-init"] else {
            throw ProbeError("Image must start its distribution's native init")
        }
        guard let stopSignal = imageConfig.config?.stopSignal else {
            throw ProbeError("Image must declare its native shutdown signal")
        }
        let shutdownSignal: Signal
        switch (entrypoint, stopSignal) {
        case (["/sbin/openrc-init"], "SIGTERM"):
            shutdownSignal = .term
        case (["/sbin/init"], "SIGRTMIN+3"):
            shutdownSignal = Signal.Linux.rtmin(offset: 3)
        default:
            throw ProbeError("Image init and native shutdown signal disagree")
        }
        _ = try await store.pull(reference: args[3], platform: .init(arch: "arm64", os: "linux"))
        var manager = try await ContainerManager(
            kernel: Kernel(path: URL(fileURLWithPath: args[2]), platform: .linuxArm),
            initfsReference: args[3], imageStore: store, network: try VmnetNetwork()
        )
        let id = UUID().uuidString
        let console = ProbeOutput(live: true)
        let container = try await manager.create(id, reference: image.reference,
            rootfsSizeInBytes: 16 * 1024 * 1024 * 1024) { @Sendable config in
                config.cpus = 4
                config.memoryInBytes = 4 * 1024 * 1024 * 1024
                config.mounts.append(.any(type: "tmpfs", source: "tmpfs", destination: "/run",
                                         options: ["nosuid", "nodev", "mode=755"]))
                config.process.arguments = entrypoint
                config.process.workingDirectory = "/"
                config.process.capabilities = .allCapabilities
                config.process.stdout = console
                config.process.stderr = console
                config.maskedPaths = []
                config.readonlyPaths = []
            }
        do {
            try await container.create()
            try await container.start()
            FileHandle.standardOutput.write(Data("{\"event\":\"started\"}\n".utf8))
            for try await line in FileHandle.standardInput.bytes.lines {
                let request = try JSONDecoder().decode(ProbeRequest.self, from: Data(line.utf8))
                guard !request.arguments.isEmpty else { break }
                let output = ProbeOutput()
                let process = try await container.exec(UUID().uuidString) { config in
                    config.arguments = request.arguments
                    config.environmentVariables = ["PATH=\(LinuxProcessConfiguration.defaultPath)", "HOME=/root"]
                    config.workingDirectory = "/"
                    config.capabilities = .allCapabilities
                    config.stdout = output
                    config.stderr = output
                }
                try await process.start()
                let status = try await process.wait(timeoutInSeconds: min(max(request.timeout ?? 30, 1), 300))
                try await process.delete()
                let response: [String: Any] = ["exitCode": status.exitCode, "output": output.text]
                let data = try JSONSerialization.data(withJSONObject: response, options: [.sortedKeys])
                FileHandle.standardOutput.write(data + Data([10]))
            }
            // Containerization.stop() releases the VM but force-kills Linux
            // processes. First prove native init can finish its own shutdown.
            let shutdownStarted = Date()
            try await container.kill(shutdownSignal)
            let shutdownStatus = try await container.wait(timeoutInSeconds: 20)
            let shutdown: [String: Any] = [
                "event": "graceful_shutdown", "exitCode": shutdownStatus.exitCode,
                "elapsedSeconds": Date().timeIntervalSince(shutdownStarted),
                "signal": stopSignal,
            ]
            FileHandle.standardOutput.write(
                try JSONSerialization.data(withJSONObject: shutdown, options: [.sortedKeys]) + Data([10]))
            // Linux reboot_pid_ns encodes HALT/POWER_OFF as SIGINT; the
            // Containerization reaper reports this normal namespace shutdown
            // as 128 + 2. A direct clean init exit remains valid too.
            guard shutdownStatus.exitCode == 0 || shutdownStatus.exitCode == 130 else {
                throw ProbeError("Native init did not exit cleanly")
            }
            try await container.stop()
            try manager.delete(id)
        } catch {
            try? await container.stop()
            try? manager.delete(id)
            throw error
        }
    }
}
