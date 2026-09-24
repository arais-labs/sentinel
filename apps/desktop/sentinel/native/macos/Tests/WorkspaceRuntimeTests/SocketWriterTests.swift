import Darwin
import Foundation
import Synchronization
import Testing
@testable import WorkspaceRuntime

struct SocketWriterTests {
    private func waitForKernelBlock(_ port: mach_port_t) throws {
        let deadline = ContinuousClock.now + .seconds(5)
        while ContinuousClock.now < deadline {
            var info = thread_basic_info_data_t()
            var count = mach_msg_type_number_t(MemoryLayout<thread_basic_info_data_t>.size / MemoryLayout<integer_t>.size)
            let result = withUnsafeMutablePointer(to: &info) { pointer in
                pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                    thread_info(port, thread_flavor_t(THREAD_BASIC_INFO), $0, &count)
                }
            }
            try #require(result == KERN_SUCCESS)
            if info.run_state == TH_STATE_WAITING { return }
            sched_yield()
        }
        try #require(Bool(false), "I/O must reach a real kernel-blocked call before cancellation")
    }

    private func pair() throws -> (FileHandle, FileHandle) {
        var sockets: [Int32] = [0, 0]
        guard socketpair(AF_UNIX, SOCK_STREAM, 0, &sockets) == 0 else {
            throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno))
        }
        for fd in sockets {
            var enabled: Int32 = 1
            setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, socklen_t(MemoryLayout<Int32>.size))
            var capacity: Int32 = 4096
            setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &capacity, socklen_t(MemoryLayout<Int32>.size))
            var deadline = timeval(tv_sec: 5, tv_usec: 0)
            setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &deadline, socklen_t(MemoryLayout<timeval>.size))
        }
        return (FileHandle(fileDescriptor: sockets[0], closeOnDealloc: true),
                FileHandle(fileDescriptor: sockets[1], closeOnDealloc: true))
    }

    @Test func thirtyTwoMiBBurstCrossesBoundedQueueAndSocketWithoutLoss() async throws {
        let (sender, receiver) = try pair()
        defer { try? sender.close(); try? receiver.close() }
        let input = BinaryInput()
        let failures = Mutex(0)
        let writer = GuestPortForward.SocketWriter(.init(sender)) { failures.withLock { $0 += 1 } }
        let expected = await withCheckedContinuation { continuation in
            DispatchQueue.global().async {
                continuation.resume(returning: Data((0..<(32 * 1024 * 1024)).map {
                    UInt8(truncatingIfNeeded: $0 ^ ($0 >> 16))
                }))
            }
        }
        let sourceDone = DispatchSemaphore(value: 0)
        let sourceFailed = Mutex(false)
        DispatchQueue.global().async {
            defer { sourceDone.signal() }
            do {
                try input.write(expected)
                input.close()
            } catch {
                sourceFailed.withLock { $0 = true }
                input.cancel()
            }
        }
        let forwarding = Task { () -> Bool in
            defer { writer.close() }
            do {
                for await data in input.stream() {
                    try await withCheckedThrowingContinuation { (completion: CheckedContinuation<Void, Error>) in
                        DispatchQueue.global().async {
                            do { try writer.write(data); completion.resume() }
                            catch { completion.resume(throwing: error) }
                        }
                    }
                }
                return true
            } catch { input.cancel(); return false }
        }
        let received = await withCheckedContinuation { continuation in
            DispatchQueue.global().async {
                var actual = Data()
                var buffer = [UInt8](repeating: 0, count: 65536)
                while true {
                    let count = buffer.withUnsafeMutableBytes {
                        Darwin.read(receiver.fileDescriptor, $0.baseAddress!, $0.count)
                    }
                    if count < 0 && errno == EINTR { continue }
                    if count < 0 {
                        shutdown(receiver.fileDescriptor, SHUT_RDWR)
                        input.cancel()
                        continuation.resume(returning: (actual, false))
                        return
                    }
                    if count == 0 { break }
                    actual.append(contentsOf: buffer.prefix(count))
                }
                continuation.resume(returning: (actual, true))
            }
        }
        #expect(received.1)
        #expect(await forwarding.value)
        let produced = await withCheckedContinuation { continuation in
            DispatchQueue.global().async {
                continuation.resume(returning: sourceDone.wait(timeout: .now() + 5) == .success)
            }
        }
        #expect(produced)
        #expect(!sourceFailed.withLock { $0 })
        #expect(failures.withLock { $0 } == 0)
        #expect(received.0 == expected)
    }

    @Test func partialSocketWritesPreserveLargePayloadAndEOF() async throws {
        try await withCheckedThrowingContinuation { (completion: CheckedContinuation<Void, Error>) in
            DispatchQueue.global().async {
                do { try self.run_partialSocketWritesPreserveLargePayloadAndEOF(); completion.resume() }
                catch { completion.resume(throwing: error) }
            }
        }
    }

    private func run_partialSocketWritesPreserveLargePayloadAndEOF() throws {
        let (sender, receiver) = try pair()
        defer { try? sender.close(); try? receiver.close() }
        let failures = Mutex(0)
        let writer = GuestPortForward.SocketWriter(.init(sender)) { failures.withLock { $0 += 1 } }
        let expected = Data((0..<(2 * 1024 * 1024 + 7)).map { UInt8(truncatingIfNeeded: $0 * 17) })
        let done = DispatchSemaphore(value: 0)
        let thrown = Mutex(false)
        DispatchQueue.global().async {
            defer { writer.close(); done.signal() }
            do { try writer.write(expected) }
            catch { thrown.withLock { $0 = true } }
        }
        var actual = Data()
        var buffer = [UInt8](repeating: 0, count: 8192)
        while true {
            let count = buffer.withUnsafeMutableBytes {
                Darwin.read(receiver.fileDescriptor, $0.baseAddress!, $0.count)
            }
            if count < 0 && errno == EINTR { continue }
            try #require(count >= 0, "Socket read failed or timed out")
            if count == 0 { break }
            actual.append(contentsOf: buffer.prefix(count))
        }
        #expect(done.wait(timeout: .now() + 5) == .success)
        #expect(!thrown.withLock { $0 })
        #expect(failures.withLock { $0 } == 0)
        #expect(actual == expected)
    }

    @Test func peerCancellationClosesSocketAndReleasesRemoteBlockedWrite() async throws {
        try await withCheckedThrowingContinuation { (completion: CheckedContinuation<Void, Error>) in
            DispatchQueue.global().async {
                do { try self.run_peerCancellationClosesSocketAndReleasesRemoteBlockedWrite(); completion.resume() }
                catch { completion.resume(throwing: error) }
            }
        }
    }

    private func run_peerCancellationClosesSocketAndReleasesRemoteBlockedWrite() throws {
        let (sender, receiver) = try pair()
        defer { try? sender.close(); try? receiver.close() }
        let failures = Mutex(0)
        let thrown = Mutex(false)
        let thread = Mutex<mach_port_t>(0)
        let started = DispatchSemaphore(value: 0), done = DispatchSemaphore(value: 0)
        let peer = GuestPortForward.Connection(receiver)
        let writer = GuestPortForward.SocketWriter(.init(sender)) { failures.withLock { $0 += 1 } }
        let payload = Data(repeating: 0x5a, count: 2 * 1024 * 1024)
        DispatchQueue.global().async {
            thread.withLock { $0 = pthread_mach_thread_np(pthread_self()) }
            started.signal()
            defer { done.signal() }
            do { try writer.write(payload) }
            catch { thrown.withLock { $0 = true } }
        }
        try #require(started.wait(timeout: .now() + 5) == .success)
        try waitForKernelBlock(thread.withLock { $0 })
        peer.cancel()
        #expect(done.wait(timeout: .now() + 5) == .success)
        #expect(thrown.withLock { $0 })
        #expect(failures.withLock { $0 } == 1)
        #expect(throws: (any Error).self) { try peer.withDescriptor { _ in } }
    }

    @Test func localCancellationReleasesSimultaneousReadAndWriteLeases() async throws {
        try await withCheckedThrowingContinuation { (completion: CheckedContinuation<Void, Error>) in
            DispatchQueue.global().async {
                do { try self.run_localCancellationReleasesSimultaneousReadAndWriteLeases(); completion.resume() }
                catch { completion.resume(throwing: error) }
            }
        }
    }

    private func run_localCancellationReleasesSimultaneousReadAndWriteLeases() throws {
        let (sender, receiver) = try pair()
        defer { try? sender.close(); try? receiver.close() }
        let connection = GuestPortForward.Connection(sender)
        let failures = Mutex(0)
        let writeFailed = Mutex(false)
        let readerPort = Mutex<mach_port_t>(0), writerPort = Mutex<mach_port_t>(0)
        let readerStarted = DispatchSemaphore(value: 0), writerStarted = DispatchSemaphore(value: 0)
        let readerDone = DispatchSemaphore(value: 0), writerDone = DispatchSemaphore(value: 0)
        let writer = GuestPortForward.SocketWriter(connection) { failures.withLock { $0 += 1 } }
        DispatchQueue.global().async {
            defer { readerDone.signal() }
            _ = try? connection.withDescriptor { fd in
                readerPort.withLock { $0 = pthread_mach_thread_np(pthread_self()) }
                readerStarted.signal()
                return try RemoteControl.readChunk(fd)
            }
        }
        let payload = Data(repeating: 0x5a, count: 2 * 1024 * 1024)
        DispatchQueue.global().async {
            writerPort.withLock { $0 = pthread_mach_thread_np(pthread_self()) }
            writerStarted.signal()
            defer { writerDone.signal() }
            do { try writer.write(payload) }
            catch { writeFailed.withLock { $0 = true } }
        }
        try #require(readerStarted.wait(timeout: .now() + 5) == .success)
        try #require(writerStarted.wait(timeout: .now() + 5) == .success)
        try waitForKernelBlock(readerPort.withLock { $0 })
        try waitForKernelBlock(writerPort.withLock { $0 })
        connection.cancel()
        #expect(readerDone.wait(timeout: .now() + 5) == .success)
        #expect(writerDone.wait(timeout: .now() + 5) == .success)
        #expect(writeFailed.withLock { $0 })
        #expect(failures.withLock { $0 } == 1)
        #expect(throws: (any Error).self) { try connection.withDescriptor { _ in } }
        connection.cancel()
    }
}
