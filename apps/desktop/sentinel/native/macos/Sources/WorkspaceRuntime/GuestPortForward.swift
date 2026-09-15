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

    final class SocketWriter: Writer {
        let file: FileHandle
        init(_ file: FileHandle) { self.file = file }
        func write(_ data: Data) throws { try file.write(contentsOf: data) }
        func close() { shutdown(file.fileDescriptor, SHUT_WR) }
    }
    struct State {
        var closed = false
        var clients: [Int32: FileHandle] = [:]
        var processes: [Int32: LinuxProcess] = [:]
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
                var timeout = timeval(tv_sec: 5, tv_usec: 0)
                setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
                let file = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
                let accepted = state.withLock { state in
                    guard !state.closed, state.clients.count < 16 else { return false }
                    state.clients[fd] = file
                    return true
                }
                guard accepted else { try? file.close(); continue }
                Task { await self.serve(file, container: container, port: port) }
            }
        }
    }

    private func serve(_ file: FileHandle, container: LinuxContainer, port: Int) async {
        let fd = file.fileDescriptor, input = BinaryInput()
        var process: LinuxProcess?
        let readers = DispatchGroup()
        defer {
            input.close()
            shutdown(fd, SHUT_RDWR)
            state.withLock { state in state.clients.removeValue(forKey: fd); state.processes.removeValue(forKey: fd) }
            readers.notify(queue: .global()) { try? file.close() }
        }
        do {
            let child = try await container.exec(UUID().uuidString) { config in
                // Send interactive input immediately; Nagle plus delayed ACKs
                // otherwise batches mouse events and VNC requests for ~40 ms.
                config.arguments = ["socat", "-", "TCP:127.0.0.1:\(port),nodelay"]
                config.environmentVariables = ["PATH=\(LinuxProcessConfiguration.defaultPath)", "HOME=/root"]
                config.stdin = input
                config.stdout = SocketWriter(file)
                config.stderr = RuntimeLog(container.id)
            }
            process = child
            guard state.withLock({ state in
                if state.closed { return false }
                state.processes[fd] = child
                return true
            }) else { throw RuntimeError("Guest forward closed") }
            try await child.start()
            readers.enter()
            DispatchQueue.global().async {
                defer { input.close(); readers.leave() }
                do {
                    while let data = try RemoteControl.readChunk(fd) { try input.write(data) }
                } catch { shutdown(fd, SHUT_RDWR) }
            }
            _ = try await child.wait()
            try? await child.delete()
        } catch {
            if let process { try? await process.kill(.kill); try? await process.delete() }
        }
    }

    func stop() async {
        let processes = state.withLock { state in
            state.closed = true
            shutdown(listener, SHUT_RDWR)
            for file in state.clients.values { shutdown(file.fileDescriptor, SHUT_RDWR) }
            return Array(state.processes.values)
        }
        for process in processes { try? await process.kill(.kill) }
        unlink(path)
    }
}
