import Foundation
import Synchronization
import Testing
@testable import WorkspaceRuntime

struct BinaryInputTests {
    @Test func byteBudgetDoesNotTreatSmallChunksAsFullSizedChunks() async throws {
        let input = BinaryInput()
        // One MiB is well below the default eight MiB budget, but contains
        // four times the legacy 256-element AsyncStream queue limit.
        let chunk = Data(repeating: 0x5a, count: 1024)
        for _ in 0..<1024 { try input.write(chunk) }
        input.close()
        var count = 0
        for await bytes in input.stream() {
            #expect(bytes.allSatisfy { $0 == 0x5a })
            count += bytes.count
        }
        #expect(count == 1024 * 1024)
    }

    @Test func boundedProducerPreservesBytesAndDrainsBeforeEOF() async throws {
        let input = BinaryInput(maxBufferedBytes: 4)
        try input.write(Data([0, 1, 2, 3]))
        let finished = DispatchSemaphore(value: 0)
        let failure = Mutex<String?>(nil)
        DispatchQueue.global().async {
            defer { finished.signal() }
            do {
                try input.write(Data([4, 5, 6, 7, 8, 9, 10]))
                input.close()
            } catch {
                failure.withLock { $0 = String(describing: error) }
                input.cancel()
            }
        }
        var actual = Data()
        for await bytes in input.stream() {
            #expect(bytes.count <= 4)
            actual.append(bytes)
        }
        let completed = await withCheckedContinuation { continuation in
            DispatchQueue.global().async {
                continuation.resume(returning: finished.wait(timeout: .now() + 5) == .success)
            }
        }
        #expect(completed)
        #expect(failure.withLock { $0 } == nil)
        #expect(actual == Data(0...10))
    }

    @Test func cancellationReleasesFullProducerAndRejectsFurtherInput() throws {
        let input = BinaryInput(maxBufferedBytes: 4)
        try input.write(Data([0, 1, 2, 3]))
        let started = DispatchSemaphore(value: 0)
        let finished = DispatchSemaphore(value: 0)
        let rejected = Mutex(false)
        DispatchQueue.global().async {
            started.signal()
            defer { finished.signal() }
            do { try input.write(Data([4, 5, 6, 7])) }
            catch { rejected.withLock { $0 = true } }
        }
        #expect(started.wait(timeout: .now() + 5) == .success)
        input.cancel()
        #expect(finished.wait(timeout: .now() + 5) == .success)
        #expect(rejected.withLock { $0 })
        #expect(throws: (any Error).self) { try input.write(Data([8])) }
    }

    @Test func cancellationEndsPendingConsumer() async {
        let input = BinaryInput(maxBufferedBytes: 4)
        let consumer = Task { () -> Data? in
            var iterator = input.stream().makeAsyncIterator()
            return await iterator.next()
        }
        input.cancel()
        #expect(await consumer.value == nil)
    }
}
