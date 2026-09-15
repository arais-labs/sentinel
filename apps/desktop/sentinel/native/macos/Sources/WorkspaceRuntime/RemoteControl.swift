import Foundation
import Synchronization
import Darwin

struct RemoteSocketUnavailable: Error {}

/// Private Unix socket transport for remote ownership. Client EOF never stops VMs.
final class RemoteControl: @unchecked Sendable {
    final class Client: Sendable {
        private struct State {
            var fd: Int32
        }
        private let state: Mutex<State>

        init(fd: Int32) {
            state = Mutex(State(fd: fd))
        }

        deinit { close() }

        func send(_ bytes: Data) {
            state.withLock { state in
                guard state.fd >= 0 else { return }
                bytes.withUnsafeBytes { buffer in
                    var sent = 0
                    while sent < buffer.count {
                        let count = Darwin.send(state.fd, buffer.baseAddress!.advanced(by: sent), buffer.count - sent, 0)
                        if count < 0 && errno == EINTR { continue }
                        guard count > 0 else {
                            // Wake the reader on a closed or stalled peer. Raw
                            // send reports socket errors through errno without
                            // accessing a Foundation handle after closure.
                            shutdown(state.fd, SHUT_RDWR)
                            return
                        }
                        sent += count
                    }
                }
            }
        }

        func close() {
            state.withLock { state in
                guard state.fd >= 0 else { return }
                Darwin.close(state.fd)
                state.fd = -1
            }
        }
    }
    struct State { var clients: [Int32: Client] = [:]; var ready = false }
    private let state = Mutex(State())
    private let listener: Int32
    let requests: AsyncStream<String>
    private let continuation: AsyncStream<String>.Continuation

    static func readChunk(_ fd: Int32) throws -> Data? {
        // Read directly into Data. Initializing a Swift byte array for every
        // chunk is expensive in debug builds and stalls graphics readback.
        var buffer = Data(count: 65536)
        while true {
            let count = buffer.withUnsafeMutableBytes {
                Darwin.read(fd, $0.baseAddress!, $0.count)
            }
            if count == 0 { return nil }
            if count < 0 {
                if errno == EINTR { continue }
                throw RuntimeError("Runtime connection closed")
            }
            buffer.count = count
            return buffer
        }
    }

    static func address(_ path: String) throws -> sockaddr_un {
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8) + [0]
        guard bytes.count <= MemoryLayout.size(ofValue: address.sun_path) else { throw RuntimeError("Runtime socket path is too long") }
        withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: bytes) }
        return address
    }

    init(path: String, answer: @escaping @Sendable (String) -> Bool = { _ in false }) throws {
        // Health probes and dropped SSH clients can close before a reply is
        // written. Keep EPIPE as a per-client write error, never a process-killing
        // signal. SO_NOSIGPIPE alone does not protect the FileHandle write path.
        signal(SIGPIPE, SIG_IGN)
        let pair = AsyncStream<String>.makeStream(bufferingPolicy: .bufferingOldest(256))
        requests = pair.stream
        continuation = pair.continuation
        listener = socket(AF_UNIX, SOCK_STREAM, 0)
        guard listener >= 0 else { throw RuntimeError("Cannot create runtime socket") }
        // The enclosing runtime store lock establishes exclusive ownership.
        unlink(path)
        var address = try Self.address(path)
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard result == 0, chmod(path, 0o600) == 0, listen(listener, 16) == 0 else {
            close(listener)
            throw RuntimeError("Cannot listen on private runtime socket")
        }
        DispatchQueue.global().async { [self] in
            while true {
                let fd = accept(listener, nil, nil)
                if fd < 0 { break }
                var noSignal: Int32 = 1
                setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size))
                // A slow client must not hold the VM control loop indefinitely.
                var timeout = timeval(tv_sec: 2, tv_usec: 0)
                setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
                let client = Client(fd: fd)
                let ready = state.withLock { $0.clients[fd] = client; return $0.ready }
                if ready { client.send(Data("{\"event\":\"ready\"}\n".utf8)) }
                DispatchQueue.global().async { [self] in
                    var pending = Data()
                    do {
                        while let data = try Self.readChunk(fd), !data.isEmpty {
                            pending.append(data)
                            if pending.count > 8 * 1024 * 1024 { break }
                            while let end = pending.firstIndex(of: 10) {
                                let line = String(decoding: pending[..<end], as: UTF8.self)
                                pending.removeSubrange(...end)
                                if answer(line) { continue }
                                if case .dropped = continuation.yield(line) { throw RuntimeError("Runtime request queue is full") }
                            }
                        }
                    } catch { }
                    _ = state.withLock { $0.clients.removeValue(forKey: fd) }
                    client.close()
                }
            }
        }
    }

    func emit(_ response: Response, bytes: Data) {
        let clients = state.withLock {
            if response.event == "ready" && response.id == nil { $0.ready = true }
            return Array($0.clients.values)
        }
        // Serialized by WorkspaceRuntime.outputLock; kernel socket buffers are bounded.
        // A snapshot can outlive a reader's disconnect. Each client serializes
        // writes with close and ignores sends after closure, including fd reuse.
        for client in clients {
            client.send(bytes)
        }
    }

    static func ping(path: String) throws {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        defer { close(fd) }
        var address = try address(path)
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard result == 0 else { throw RuntimeError("Remote runtime is not running") }
    }

    static func relay(path: String) throws {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        var address = try address(path)
        let connected = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connected == 0 else {
            let code = errno
            close(fd)
            if code == ENOENT || code == ECONNREFUSED { throw RemoteSocketUnavailable() }
            throw RuntimeError("Cannot connect to remote runtime socket: \(String(cString: strerror(code)))")
        }
        signal(SIGPIPE, SIG_IGN)
        let socketFile = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        // Transport connected is distinct from service initialization completing.
        try FileHandle.standardOutput.write(contentsOf: Data("{\"event\":\"connected\"}\n".utf8))
        DispatchQueue.global().async {
            do {
                while let data = try Self.readChunk(STDIN_FILENO), !data.isEmpty {
                    try socketFile.write(contentsOf: data)
                }
            } catch { }
            shutdown(fd, SHUT_RDWR)
        }
        while let data = try Self.readChunk(fd), !data.isEmpty {
            try FileHandle.standardOutput.write(contentsOf: data)
        }
    }
}
