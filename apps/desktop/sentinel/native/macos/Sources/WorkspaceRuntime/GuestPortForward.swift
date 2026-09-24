import Containerization
import CryptoKit
import Foundation
import Synchronization
import Darwin

/// Private, byte-preserving guest TCP forwarding. No output events or base64.
final class GuestPortForward: @unchecked Sendable {
    static func socketPath(root: URL, workspace: String, port: Int) throws -> String {
        let name = "\(workspace)-\(port).sock"
        var directory = root.appendingPathComponent("forwards")
        if directory.appendingPathComponent(name).path.utf8.count >= 104 {
            let digest = SHA256.hash(data: Data(root.path.utf8)).map { String(format: "%02x", $0) }.joined()
            directory = URL(fileURLWithPath: "/tmp/sentinel-forward-" + digest.prefix(16))
        }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        var info = stat()
        guard lstat(directory.path, &info) == 0, info.st_uid == getuid(),
              info.st_mode & S_IFMT == S_IFDIR, info.st_mode & 0o077 == 0 else {
            throw RuntimeError("Guest forward directory must be private and owned by this user")
        }
        return directory.appendingPathComponent(name).path
    }

    final class Connection: @unchecked Sendable {
        private struct State {
            var active = 0
            var closed = false
            var released = false
        }
        private let state = Mutex(State())
        private let file: FileHandle
        let identifier: Int32

        init(_ file: FileHandle) {
            self.file = file
            identifier = file.fileDescriptor
        }

        func withDescriptor<T>(_ body: (Int32) throws -> T) throws -> T {
            let accepted = state.withLock { state in
                if state.closed { return false }
                state.active += 1
                return true
            }
            guard accepted else { throw RuntimeError("Guest forward connection closed") }
            defer {
                let release = state.withLock { state in
                    state.active -= 1
                    if state.closed && state.active == 0 && !state.released {
                        state.released = true
                        return true
                    }
                    return false
                }
                if release { try? file.close() }
            }
            return try body(identifier)
        }

        func finishWriting() {
            _ = try? withDescriptor { shutdown($0, SHUT_WR) }
        }

        func cancel() {
            let release = state.withLock { state in
                if !state.closed {
                    state.closed = true
                    shutdown(identifier, SHUT_RDWR)
                }
                if state.active == 0 && !state.released {
                    state.released = true
                    return true
                }
                return false
            }
            if release { try? file.close() }
        }

        deinit { cancel() }
    }

    final class SocketWriter: Writer {
        let connection: Connection
        let failure: @Sendable () -> Void
        init(_ connection: Connection, failure: @escaping @Sendable () -> Void) {
            self.connection = connection
            self.failure = failure
        }
        func write(_ data: Data) throws {
            do {
                try connection.withDescriptor { fd in
                    try data.withUnsafeBytes { bytes in
                        var offset = 0
                        while offset < bytes.count {
                            let count = Darwin.send(fd, bytes.baseAddress!.advanced(by: offset), bytes.count - offset, 0)
                            if count < 0 && errno == EINTR { continue }
                            guard count > 0 else { throw RuntimeError("Guest forward connection closed") }
                            offset += count
                        }
                    }
                }
            } catch {
                failure()
                throw error
            }
        }
        func close() { connection.finishWriting() }
    }
    struct State {
        var closed = false
        var clients: [Int32: Connection] = [:]
        var processes: [Int32: LinuxProcess] = [:]
        var inputs: [Int32: BinaryInput] = [:]
    }
    private let state = Mutex(State())
    private let listener: Int32
    let path: String

    init(path: String, container: LinuxContainer, port: Int) throws {
        self.path = path
        listener = socket(AF_UNIX, SOCK_STREAM, 0)
        guard listener >= 0 else { throw RuntimeError("Cannot create guest forward") }
        do {
            var address = try RemoteControl.address(path)
            unlink(path)
            let result = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
            }
            guard result == 0, chmod(path, 0o600) == 0, listen(listener, 16) == 0 else {
                throw RuntimeError("Cannot listen on private guest forward")
            }
        } catch { Darwin.close(listener); throw error }
        DispatchQueue.global().async { [self] in
            defer { Darwin.close(listener) }
            while !state.withLock({ $0.closed }) {
                let fd = accept(listener, nil, nil)
                if fd < 0 { break }
                var noSignal: Int32 = 1
                setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size))
                let file = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
                let connection = Connection(file)
                let accepted = state.withLock { state in
                    guard !state.closed, state.clients.count < 16 else { return false }
                    state.clients[fd] = connection
                    return true
                }
                guard accepted else { connection.cancel(); continue }
                Task { await self.serve(connection, container: container, port: port) }
            }
        }
    }

    private func serve(_ connection: Connection, container: LinuxContainer, port: Int) async {
        let fd = connection.identifier, input = BinaryInput()
        var process: LinuxProcess?
        defer {
            input.cancel()
            connection.cancel()
            state.withLock { state in
                guard state.clients[fd] === connection else { return }
                state.clients.removeValue(forKey: fd)
                state.processes.removeValue(forKey: fd)
                state.inputs.removeValue(forKey: fd)
            }
        }
        do {
            guard state.withLock({ state in
                if state.closed || state.clients[fd] !== connection { return false }
                state.inputs[fd] = input
                return true
            }) else { throw RuntimeError("Guest forward closed") }
            let child = try await container.exec(UUID().uuidString) { config in
                // Send interactive input immediately; Nagle plus delayed ACKs
                // otherwise batches mouse events and VNC requests for ~40 ms.
                config.arguments = ["socat", "-", "TCP:127.0.0.1:\(port),nodelay"]
                config.environmentVariables = ["PATH=\(LinuxProcessConfiguration.defaultPath)", "HOME=/root"]
                config.stdin = input
                config.stdout = SocketWriter(connection) { self.abort(connection) }
                config.stderr = RuntimeLog(container.id)
            }
            process = child
            guard state.withLock({ state in
                if state.closed || state.clients[fd] !== connection { return false }
                state.processes[fd] = child
                return true
            }) else { throw RuntimeError("Guest forward closed") }
            try await child.start()
            DispatchQueue.global().async {
                defer { input.close() }
                do {
                    while let data = try connection.withDescriptor({ try RemoteControl.readChunk($0) }) {
                        try input.write(data)
                    }
                } catch { self.abort(connection) }
            }
            _ = try await child.wait()
            try? await child.delete()
        } catch {
            if let process { try? await process.kill(.kill); try? await process.delete() }
        }
    }

    private func abort(_ connection: Connection) {
        let fd = connection.identifier
        connection.cancel()
        let endpoint = state.withLock { state -> (BinaryInput?, LinuxProcess?) in
            guard state.clients[fd] === connection else { return (nil, nil) }
            return (state.inputs[fd], state.processes[fd])
        }
        endpoint.0?.cancel()
        if let process = endpoint.1 { Task { try? await process.kill(.kill) } }
    }

    func stop() async {
        let pending = state.withLock { state in
            state.closed = true
            shutdown(listener, SHUT_RDWR)
            for connection in state.clients.values { connection.cancel() }
            return (Array(state.processes.values), Array(state.inputs.values))
        }
        for input in pending.1 { input.cancel() }
        for process in pending.0 { try? await process.kill(.kill) }
        unlink(path)
    }
}
