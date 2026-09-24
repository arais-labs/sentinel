import Darwin
import Foundation
import Synchronization
import Testing
@testable import WorkspaceRuntime

struct GuestVsockForwardTests {
    private func configure(_ fd: Int32) throws {
        var enabled: Int32 = 1
        try #require(setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, socklen_t(MemoryLayout<Int32>.size)) == 0)
        var capacity: Int32 = 4096
        try #require(setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &capacity, socklen_t(MemoryLayout<Int32>.size)) == 0)
        let flags = fcntl(fd, F_GETFL)
        try #require(flags >= 0 && fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0)
    }

    private func pair() throws -> (FileHandle, FileHandle) {
        var sockets: [Int32] = [-1, -1]
        try #require(socketpair(AF_UNIX, SOCK_STREAM, 0, &sockets) == 0)
        let first = FileHandle(fileDescriptor: sockets[0], closeOnDealloc: true)
        let second = FileHandle(fileDescriptor: sockets[1], closeOnDealloc: true)
        try configure(sockets[0])
        try configure(sockets[1])
        return (first, second)
    }

    private func connect(_ path: String) throws -> FileHandle {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        try #require(fd >= 0)
        let file = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        var address = try RemoteControl.address(path)
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        try #require(result == 0)
        try configure(fd)
        return file
    }

    private func directory() throws -> URL {
        let url = URL(fileURLWithPath: "/tmp/sentinel-vsock-test-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: false,
                                               attributes: [.posixPermissions: 0o700])
        return url
    }

    private func wait(_ fd: Int32, _ events: Int16, until deadline: UInt64) throws {
        while true {
            let now = DispatchTime.now().uptimeNanoseconds
            try #require(now < deadline, "Socket regression deadline expired")
            var item = pollfd(fd: fd, events: events, revents: 0)
            let milliseconds = Int32(min((deadline - now) / 1_000_000 + 1, 15_000))
            let result = poll(&item, 1, milliseconds)
            if result < 0 && errno == EINTR { continue }
            try #require(result > 0, "Socket operation did not become ready")
            return
        }
    }

    private func write(_ fd: Int32, _ data: Data, from start: Int = 0) throws {
        let deadline = DispatchTime.now().uptimeNanoseconds + 15_000_000_000
        try data.withUnsafeBytes { bytes in
            var offset = start
            while offset < bytes.count {
                let count = Darwin.send(fd, bytes.baseAddress!.advanced(by: offset), bytes.count - offset, 0)
                if count < 0 && errno == EINTR { continue }
                if count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) {
                    try wait(fd, Int16(POLLOUT), until: deadline)
                    continue
                }
                guard count > 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
                offset += count
            }
        }
    }

    private func read(_ fd: Int32, count: Int) throws -> Data {
        let deadline = DispatchTime.now().uptimeNanoseconds + 15_000_000_000
        var result = Data()
        var buffer = [UInt8](repeating: 0, count: 65536)
        while result.count < count {
            let received = buffer.withUnsafeMutableBytes {
                Darwin.recv(fd, $0.baseAddress!, min($0.count, count - result.count), 0)
            }
            if received < 0 && errno == EINTR { continue }
            if received < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) {
                try wait(fd, Int16(POLLIN), until: deadline)
                continue
            }
            try #require(received > 0, "Socket ended before the expected payload")
            result.append(contentsOf: buffer.prefix(received))
        }
        return result
    }

    private func eof(_ fd: Int32) throws -> Bool {
        try wait(fd, Int16(POLLIN), until: DispatchTime.now().uptimeNanoseconds + 5_000_000_000)
        var byte: UInt8 = 0
        let count = Darwin.recv(fd, &byte, 1, 0)
        return count == 0 || (count < 0 && errno == ECONNRESET)
    }

    private func fillUntilBlocked(_ fd: Int32, _ data: Data) throws -> Int {
        try data.withUnsafeBytes { bytes in
            var offset = 0
            while offset < bytes.count {
                let count = Darwin.send(fd, bytes.baseAddress!.advanced(by: offset), bytes.count - offset, 0)
                if count < 0 && errno == EINTR { continue }
                if count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) { return offset }
                try #require(count > 0)
                offset += count
            }
            throw NSError(domain: "ExpectedBackpressure", code: 1)
        }
    }

    private func payload(_ size: Int) -> Data {
        var bytes = Data(count: size)
        bytes.withUnsafeMutableBytes { storage in
            let words = storage.bindMemory(to: UInt64.self)
            for index in words.indices { words[index] = UInt64(index) &* 0x9e3779b97f4a7c15 }
        }
        return bytes
    }

    @Test func asymmetricThirtyTwoMiBChannelsProgressIndependently() async throws {
        try await withCheckedThrowingContinuation { (completion: CheckedContinuation<Void, Error>) in
            DispatchQueue.global().async {
                do { try self.run_asymmetricThirtyTwoMiBChannelsProgressIndependently(); completion.resume() }
                catch { completion.resume(throwing: error) }
            }
        }
    }

    private func run_asymmetricThirtyTwoMiBChannelsProgressIndependently() throws {
        let root = try directory(), path = root.appendingPathComponent("gpu.sock").path
        defer { try? FileManager.default.removeItem(at: root) }
        let (dialA, peerA) = try pair(), (dialB, peerB) = try pair()
        defer { try? peerA.close(); try? peerB.close() }
        let peers = Mutex([dialA, dialB])
        let forward = try GuestVsockForward(path: path) { peers.withLock { $0.removeFirst() } }
        defer { forward.stop() }
        let clientA = try connect(path)
        defer { try? clientA.close() }
        try write(clientA.fileDescriptor, Data([1]))
        #expect(try read(peerA.fileDescriptor, count: 1) == Data([1]))
        let clientB = try connect(path)
        defer { try? clientB.close() }
        try write(clientB.fileDescriptor, Data([2]))
        #expect(try read(peerB.fileDescriptor, count: 1) == Data([2]))

        let expected = payload(32 * 1024 * 1024)
        let offset = try fillUntilBlocked(clientA.fileDescriptor, expected)
        let doneA = DispatchGroup(), doneB = DispatchGroup()
        doneA.enter(); doneB.enter()
        defer {
            forward.stop()
            shutdown(clientA.fileDescriptor, SHUT_RDWR)
            shutdown(peerB.fileDescriptor, SHUT_RDWR)
            #expect(doneA.wait(timeout: .now() + 5) == .success)
            #expect(doneB.wait(timeout: .now() + 5) == .success)
        }
        let failures = Mutex<[String]>([])
        DispatchQueue.global().async {
            defer { doneA.leave() }
            do { try write(clientA.fileDescriptor, expected, from: offset) }
            catch { failures.withLock { $0.append(String(describing: error)) } }
        }
        DispatchQueue.global().async {
            defer { doneB.leave() }
            do { try write(peerB.fileDescriptor, expected) }
            catch { failures.withLock { $0.append(String(describing: error)) } }
        }
        // A's destination deliberately stays unread while B transfers in the
        // opposite direction. B must not share A's backpressure queue.
        try write(clientB.fileDescriptor, Data([0x5a]))
        #expect(try read(peerB.fileDescriptor, count: 1) == Data([0x5a]))
        #expect(try read(clientB.fileDescriptor, count: expected.count) == expected)
        #expect(doneB.wait(timeout: .now() + 5) == .success)
        #expect(doneA.wait(timeout: .now()) == .timedOut)
        #expect(try read(peerA.fileDescriptor, count: expected.count) == expected)
        #expect(doneA.wait(timeout: .now() + 5) == .success)
        #expect(failures.withLock { $0.isEmpty })
    }

    @Test func stopReleasesBlockedReaderAndBackpressuredWriter() async throws {
        try await withCheckedThrowingContinuation { (completion: CheckedContinuation<Void, Error>) in
            DispatchQueue.global().async {
                do { try self.run_stopReleasesBlockedReaderAndBackpressuredWriter(); completion.resume() }
                catch { completion.resume(throwing: error) }
            }
        }
    }

    private func run_stopReleasesBlockedReaderAndBackpressuredWriter() throws {
        let root = try directory(), path = root.appendingPathComponent("gpu.sock").path
        defer { try? FileManager.default.removeItem(at: root) }
        let (dial, peer) = try pair()
        defer { try? peer.close() }
        let forward = try GuestVsockForward(path: path) { dial }
        defer { forward.stop() }
        let client = try connect(path)
        defer { try? client.close() }
        try write(client.fileDescriptor, Data([1]))
        _ = try read(peer.fileDescriptor, count: 1)
        let bytes = payload(32 * 1024 * 1024)
        let offset = try fillUntilBlocked(client.fileDescriptor, bytes)
        let readerEntered = DispatchSemaphore(value: 0), readerDone = DispatchGroup()
        let writerEntered = DispatchSemaphore(value: 0), writerDone = DispatchGroup()
        readerDone.enter(); writerDone.enter()
        defer {
            forward.stop()
            shutdown(client.fileDescriptor, SHUT_RDWR)
            #expect(readerDone.wait(timeout: .now() + 5) == .success)
            #expect(writerDone.wait(timeout: .now() + 5) == .success)
        }
        let readerClosed = Mutex(false), writerFailed = Mutex(false)
        DispatchQueue.global().async {
            readerEntered.signal()
            defer { readerDone.leave() }
            readerClosed.withLock { $0 = (try? eof(client.fileDescriptor)) == true }
        }
        DispatchQueue.global().async {
            writerEntered.signal()
            defer { writerDone.leave() }
            do { try write(client.fileDescriptor, bytes, from: offset) }
            catch { writerFailed.withLock { $0 = true } }
        }
        try #require(readerEntered.wait(timeout: .now() + 5) == .success)
        try #require(writerEntered.wait(timeout: .now() + 5) == .success)
        forward.stop()
        #expect(readerDone.wait(timeout: .now() + 5) == .success)
        #expect(writerDone.wait(timeout: .now() + 5) == .success)
        #expect(readerClosed.withLock { $0 })
        #expect(writerFailed.withLock { $0 })
    }

    @Test func lateDialAfterStopClosesBothOwnedEndpoints() async throws {
        try await withCheckedThrowingContinuation { (completion: CheckedContinuation<Void, Error>) in
            DispatchQueue.global().async {
                do { try self.run_lateDialAfterStopClosesBothOwnedEndpoints(); completion.resume() }
                catch { completion.resume(throwing: error) }
            }
        }
    }

    private func run_lateDialAfterStopClosesBothOwnedEndpoints() throws {
        let root = try directory(), path = root.appendingPathComponent("gpu.sock").path
        defer { try? FileManager.default.removeItem(at: root) }
        let entered = DispatchSemaphore(value: 0)
        let pending = Mutex<CheckedContinuation<FileHandle, any Error>?>(nil)
        defer {
            let abandoned = pending.withLock { value in
                defer { value = nil }
                return value
            }
            abandoned?.resume(throwing: CancellationError())
        }
        let forward = try GuestVsockForward(path: path) {
            try await withCheckedThrowingContinuation { continuation in
                pending.withLock { $0 = continuation }
                entered.signal()
            }
        }
        defer { forward.stop() }
        let client = try connect(path)
        defer { try? client.close() }
        try #require(entered.wait(timeout: .now() + 5) == .success)
        forward.stop()
        #expect(try eof(client.fileDescriptor))
        let (dial, peer) = try pair()
        defer { try? peer.close() }
        let continuation = try #require(pending.withLock { value in
            defer { value = nil }
            return value
        })
        // Deliberately emulate the upstream non-cancellable VZ callback.
        continuation.resume(returning: dial)
        #expect(try eof(peer.fileDescriptor))
    }

    @Test func duplicateStopCannotUnlinkReplacementListener() async throws {
        try await withCheckedThrowingContinuation { (completion: CheckedContinuation<Void, Error>) in
            DispatchQueue.global().async {
                do { try self.run_duplicateStopCannotUnlinkReplacementListener(); completion.resume() }
                catch { completion.resume(throwing: error) }
            }
        }
    }

    private func run_duplicateStopCannotUnlinkReplacementListener() throws {
        let root = try directory(), path = root.appendingPathComponent("gpu.sock").path
        defer { try? FileManager.default.removeItem(at: root) }
        let old = try GuestVsockForward(path: path) { throw CancellationError() }
        old.stop()
        let (dial, peer) = try pair()
        defer { try? peer.close() }
        let replacement = try GuestVsockForward(path: path) { dial }
        defer { replacement.stop() }
        old.stop()
        let client = try connect(path)
        defer { try? client.close() }
        try write(client.fileDescriptor, Data([0x31, 0x72]))
        #expect(try read(peer.fileDescriptor, count: 2) == Data([0x31, 0x72]))
    }
}
