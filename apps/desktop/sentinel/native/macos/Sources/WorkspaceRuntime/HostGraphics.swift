import Containerization
import Foundation
import Synchronization
import Darwin

/// Binary guest input, bounded independently of the JSON command channel.
final class BinaryInput: ReaderStream {
    let pair = AsyncStream<Data>.makeStream(bufferingPolicy: .bufferingOldest(256))
    func stream() -> AsyncStream<Data> { pair.stream }
    func write(_ data: Data) throws {
        for offset in stride(from: 0, to: data.count, by: 32768) {
            let chunk = data.subdata(in: offset..<min(offset + 32768, data.count))
            switch pair.continuation.yield(chunk) {
            case .enqueued: break
            case .dropped, .terminated:
                pair.continuation.finish()
                throw RuntimeError("Guest binary stream is closed or too slow")
            @unknown default: throw RuntimeError("Guest binary stream failed")
            }
        }
    }
    func close() { pair.continuation.finish() }
}

/// Runs on the VM owner. Graphics commands and pixel readbacks never cross SSH.
final class HostGraphics: Writer, @unchecked Sendable {
    final class Client: @unchecked Sendable {
        let process: Process
        let file: FileHandle
        let closed = Mutex(false)
        init(process: Process, fd: Int32) {
            self.process = process
            self.file = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        }
        func stop() {
            guard closed.withLock({ value in if value { return false }; value = true; return true }) else { return }
            shutdown(file.fileDescriptor, SHUT_RDWR)
            if process.isRunning { process.terminate() }
        }
    }
    struct State {
        var pending = Data()
        var clients: [UInt32: Client] = [:]
        var ready = false
        var closed = false
    }
    let input = BinaryInput()
    private let state = Mutex(State())
    private let sends = Mutex(0)
    private let directory: URL
    private let resources: URL
    private var guest: LinuxProcess?
    var active: Bool { state.withLock { !$0.closed && $0.ready } }

    init(resources: URL) throws {
        self.resources = resources
        guard FileManager.default.isExecutableFile(atPath: resources.appendingPathComponent("sentinel-graphics-renderer").path) else {
            throw RuntimeError("Update this machine's Sentinel Runtime to install host graphics")
        }
        // macOS's per-user temporary path can exceed sockaddr_un's 104 bytes.
        directory = URL(fileURLWithPath: "/tmp").appendingPathComponent("sentinel-gfx-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
    }

    func start(container: LinuxContainer) async throws {
        let source = try String(contentsOf: resources.appendingPathComponent("guest-bridge.py"), encoding: .utf8)
        let process = try await container.exec(UUID().uuidString) { [self] config in
            config.arguments = ["python3", "-u", "-c", source]
            config.environmentVariables = ["PATH=\(LinuxProcessConfiguration.defaultPath)", "HOME=/root"]
            config.stdin = input
            config.stdout = self
            config.stderr = RuntimeLog(container.id)
        }
        guest = process
        do {
            try await process.start()
            Task { [self] in
                _ = try? await process.wait()
                close()
                try? await process.delete()
            }
            for _ in 0..<1000 {
                if active { return }
                if state.withLock({ $0.closed }) { break }
                try await Task.sleep(for: .milliseconds(10))
            }
            throw RuntimeError("Host graphics connection did not become ready")
        } catch {
            await stop()
            throw error
        }
    }

    private func send(_ kind: UInt8, _ id: UInt32, _ data: Data = Data()) throws {
        try sends.withLock { _ in
            var frame = Data([kind])
            var client = id.bigEndian, size = UInt32(data.count).bigEndian
            withUnsafeBytes(of: &client) { frame.append(contentsOf: $0) }
            withUnsafeBytes(of: &size) { frame.append(contentsOf: $0) }
            frame.append(data)
            try input.write(frame)
        }
    }

    func write(_ data: Data) throws {
        try state.withLock { state in
            guard !state.closed else { throw RuntimeError("Host graphics stopped") }
            state.pending.append(data)
            guard state.pending.count <= 8 * 1024 * 1024 else { throw RuntimeError("Graphics frame limit exceeded") }
            while state.pending.count >= 9 {
                let bytes = [UInt8](state.pending.prefix(9))
                let kind = bytes[0]
                let id = bytes[1..<5].reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
                let size = Int(bytes[5..<9].reduce(UInt32(0)) { ($0 << 8) | UInt32($1) })
                guard (1...4).contains(kind), size <= 32768, kind == 2 || size == 0 else { throw RuntimeError("Invalid graphics frame") }
                guard state.pending.count >= size + 9 else { return }
                let payload = Data(state.pending.dropFirst(9).prefix(size))
                state.pending.removeFirst(size + 9)
                switch kind {
                case 4: state.ready = true
                case 1:
                    guard state.clients[id] == nil, state.clients.count < 16 else { throw RuntimeError("Too many graphics clients") }
                    state.clients[id] = try open(id)
                case 2:
                    if let client = state.clients[id] { try client.file.write(contentsOf: payload) }
                case 3: state.clients.removeValue(forKey: id)?.stop()
                default: break
                }
            }
        }
    }

    private func open(_ id: UInt32) throws -> Client {
        let socketPath = directory.appendingPathComponent("\(id).sock").path
        let process = Process()
        process.executableURL = resources.appendingPathComponent("sentinel-graphics-renderer")
        process.arguments = ["--no-loop-or-fork", "--use-egl-surfaceless", "--use-gles", "--socket-path", socketPath]
        process.environment = ["PATH": "/usr/bin:/bin", "HOME": directory.path, "TMPDIR": directory.path,
                               "XDG_CACHE_HOME": directory.path, "ANGLE_DEFAULT_PLATFORM": "metal"]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        do {
            for _ in 0..<100 {
                let fd = socket(AF_UNIX, SOCK_STREAM, 0)
                guard fd >= 0 else { throw RuntimeError("Cannot create graphics socket") }
                var address = try RemoteControl.address(socketPath)
                let result = withUnsafePointer(to: &address) {
                    $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
                }
                if result == 0 {
                    var noSignal: Int32 = 1
                    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size))
                    var timeout = timeval(tv_sec: 2, tv_usec: 0)
                    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
                    let client = Client(process: process, fd: fd)
                    DispatchQueue.global().async { [self] in
                        defer {
                            client.stop()
                            try? client.file.close()
                            try? send(3, id)
                        }
                        do {
                            while !client.closed.withLock({ $0 }), let data = try RemoteControl.readChunk(fd) {
                                for offset in stride(from: 0, to: data.count, by: 32768) {
                                    try send(2, id, data.subdata(in: offset..<min(offset + 32768, data.count)))
                                }
                            }
                        } catch { }
                    }
                    return client
                }
                Darwin.close(fd)
                if !process.isRunning { break }
                Thread.sleep(forTimeInterval: 0.025)
            }
            throw RuntimeError("Metal renderer did not open its socket")
        } catch {
            if process.isRunning { process.terminate() }
            throw error
        }
    }

    func close() {
        let clients = state.withLock { state in
            state.closed = true
            let clients = Array(state.clients.values)
            state.clients.removeAll()
            return clients
        }
        input.close()
        clients.forEach { $0.stop() }
        try? FileManager.default.removeItem(at: directory)
    }
    func stop() async {
        close()
        if let guest { try? await guest.kill(.kill) }
    }
}
