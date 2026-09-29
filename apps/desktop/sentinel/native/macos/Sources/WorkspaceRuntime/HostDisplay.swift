import Containerization
import Foundation
import Synchronization
import Darwin

/// A one-shot startup signal, driven by the child process rather than polling.
final class DisplayReadiness: Writer {
    private let buffer = Mutex(Data())
    private let endpoints: DisplayEndpointOwnership?
    init(endpoints: DisplayEndpointOwnership? = nil) { self.endpoints = endpoints }
    let events = AsyncStream<Bool>.makeStream(bufferingPolicy: .bufferingNewest(1))
    func write(_ bytes: Data) throws {
        try buffer.withLock { buffer in
            buffer.append(bytes)
            guard buffer.count < 8192 else { throw RuntimeError("Invalid display startup response") }
            while let newline = buffer.firstIndex(of: 10) {
                let data = Data(buffer[..<newline])
                buffer.removeSubrange(...newline)
                if let event = try JSONSerialization.jsonObject(with: data) as? [String: String] {
                    endpoints?.record(event)
                    if event["event"] == "ready" { events.continuation.yield(true) }
                }
            }
        }
    }
    func close() { events.continuation.finish() }
    func wait() async throws {
        for await ready in events.stream { if ready { return } }
        throw RuntimeError("Virtual display exited before becoming ready")
    }

    /// Stream short reports immediately, then complete only after EOF/error.
    func read(from handle: FileHandle) async {
        await withCheckedContinuation { (completion: CheckedContinuation<Void, Never>) in
            // A renderer can keep stdout open for the desktop's entire lifetime.
            DispatchQueue(label: "us.arais.sentinel.desktop.output").async {
                defer { self.close(); try? handle.close(); completion.resume() }
                var bytes = [UInt8](repeating: 0, count: 4096)
                while true {
                    // FileHandle.read(upToCount:) may wait to fill its buffer on
                    // macOS. One POSIX read delivers the available ready event.
                    let count = Darwin.read(handle.fileDescriptor, &bytes, bytes.count)
                    if count < 0 && errno == EINTR { continue }
                    guard count > 0 else { return }
                    do { try self.write(Data(bytes.prefix(count))) }
                    catch { return }
                }
            }
        }
    }
}

/// The VM owner runs the GPU proxy, Metal renderer and encoder as one unit.
/// A video/control connection owns no part of this lifecycle.
final class HostDisplay: @unchecked Sendable {
    private let resources: URL
    private let directory: URL
    let videoPath: String
    let controlPath: String
    private let closed = Mutex(false)
    private var renderer: Process?
    private var rendererOutput: Task<Void, Never>?
    private let endpoints: DisplayEndpointOwnership
    private var forwarding: GuestVsockForward?
    private var guests: [LinuxProcess] = []
    private var container: LinuxContainer?
    var active: Bool { !closed.withLock { $0 } && renderer?.isRunning == true }

    static func controlPath(root: URL, workspace: String) throws -> String {
        try GuestPortForward.socketPath(root: root, workspace: workspace + "-display", port: 0)
    }

    init(resources: URL, videoPath: String, controlPath: String) throws {
        self.resources = resources
        self.videoPath = videoPath
        self.controlPath = controlPath
        endpoints = DisplayEndpointOwnership(paths: [videoPath, controlPath])
        directory = URL(fileURLWithPath: "/tmp/sentinel-display-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                               attributes: [.posixPermissions: 0o700])
    }

    func start(container: LinuxContainer, width: Int, height: Int, fps: Int = 120) async throws {
        self.container = container
        guard (320...4096).contains(width), (240...4096).contains(height), width * height <= 16_000_000,
              width % 2 == 0, height % 2 == 0, (1...120).contains(fps) else { throw RuntimeError("Invalid desktop dimensions") }
        let caps = directory.appendingPathComponent("virgl.capset")
        do {
            let capabilityProcess = native([caps.path, "--capabilities"])
            let status = try await Task.detached {
                try capabilityProcess.run()
                capabilityProcess.waitUntilExit()
                return capabilityProcess.terminationStatus
            }.value
            guard status == 0 else { throw RuntimeError("Virtual GPU capabilities unavailable") }
            try Task.checkCancellation()
            let input = BinaryInput()
            try input.write(Data(contentsOf: caps)); input.close()
            let copy = try await container.exec(UUID().uuidString) { config in
                config.arguments = ["sh", "-c", "mkdir -p /run/sentinel-desktop && chmod 700 /run/sentinel-desktop && cat > /run/sentinel-desktop/virgl.capset"]
                config.stdin = input
                config.stderr = RuntimeLog(container.id)
            }
            try await copy.start()
            let copied = try await copy.wait()
            try await copy.delete()
            guard copied.exitCode == 0 else { throw RuntimeError("Cannot install GPU capabilities") }

            let bridgeReady = DisplayReadiness()
            try await startGuest(container, arguments: ["/opt/sentinel/graphics/bin/sentinel-gpu-bridge"], readiness: bridgeReady)
            try await bridgeReady.wait()
            let forwarding = try GuestVsockForward(path: directory.appendingPathComponent("gpu.sock").path,
                                                  container: container, port: 55668)
            self.forwarding = forwarding
            let ready = DisplayReadiness(endpoints: endpoints)
            let process = native([caps.path, forwarding.path, videoPath, controlPath, String(width), String(height), String(fps), "--serve"])
            let output = Pipe()
            process.standardOutput = output
            renderer = process
            try process.run()
            try output.fileHandleForWriting.close()
            rendererOutput = Task {
                await ready.read(from: output.fileHandleForReading)
            }
            let launcher = try String(contentsOf: resources.appendingPathComponent("gpu-start.sh"), encoding: .utf8)
            try await startGuest(container, arguments: ["sh", "-c", launcher, "sentinel-gpu", String(width), String(height)], onExit: { ready.close() })
            try await ready.wait()
            try Task.checkCancellation()
        } catch { await stop(); throw error }
    }

    private func native(_ arguments: [String]) -> Process {
        let process = Process()
        process.executableURL = resources.appendingPathComponent("sentinel-desktop-renderer")
        process.arguments = arguments
        process.environment = ["PATH": "/usr/bin:/bin", "HOME": directory.path, "TMPDIR": directory.path,
                               "XDG_CACHE_HOME": directory.path]
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.standardError
        return process
    }

    private func startGuest(_ container: LinuxContainer, arguments: [String], readiness: DisplayReadiness? = nil,
                            onExit: (@Sendable () -> Void)? = nil) async throws {
        let process = try await container.exec(UUID().uuidString) { config in
            config.arguments = arguments
            config.capabilities = .allCapabilities
            config.environmentVariables = ["PATH=\(LinuxProcessConfiguration.defaultPath)", "HOME=/root"]
            config.stdout = readiness ?? RuntimeLog(container.id) as any Writer
            config.stderr = RuntimeLog(container.id)
        }
        guests.append(process)
        try await process.start()
        Task {
            _ = try? await process.wait()
            readiness?.close()
            onExit?()
            try? await process.delete()
        }
    }

    func close() {
        guard closed.withLock({ if $0 { return false }; $0 = true; return true }) else { return }
        if let renderer, renderer.isRunning { renderer.terminate() }
    }

    func stop(guestAvailable: Bool = true) async {
        // Let the compositor release DRM while its renderer is still alive.
        // Recovery has already powered the VM off and must not call the guest.
        if guestAvailable, let container, active {
            _ = try? await WorkspaceRuntime.executeGuest(Request(id: UUID().uuidString, action: "exec", workspace: container.id,
                arguments: ["python3", "/opt/sentinel/desktop/desktop-session.py", "{\"action\":\"stop\"}"], timeout: 10), container: container)
        }
        close()
        forwarding?.stop()
        for process in guests { try? await process.kill(.kill) }
        // Kernel GPU device teardown belongs to VM shutdown, never viewer detach.
        if let renderer { await Task.detached { if renderer.isRunning { renderer.waitUntilExit() } }.value }
        await rendererOutput?.value
        endpoints.removeOwned()
        try? FileManager.default.removeItem(at: directory)
    }
}
