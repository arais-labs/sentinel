import Containerization
import Foundation

/// Binary guest input, bounded independently of the JSON command channel.
final class BinaryInput: ReaderStream, @unchecked Sendable {
    private let condition = NSCondition()
    private let producer = NSLock()
    private let capacity: Int
    private var pending: [Data?] = []
    private var head = 0
    private var bufferedBytes = 0
    private var closed = false
    private var consumer: CheckedContinuation<Data?, Never>?

    init(maxBufferedBytes: Int = 8 * 1024 * 1024) {
        precondition(maxBufferedBytes > 0)
        capacity = maxBufferedBytes
    }

    func stream() -> AsyncStream<Data> {
        AsyncStream(unfolding: {
            await withCheckedContinuation { self.next($0) }
        }, onCancel: { self.cancel() })
    }

    private func next(_ continuation: CheckedContinuation<Data?, Never>) {
        condition.lock()
        if head < pending.count {
            let data = pending[head]!
            pending[head] = nil
            head += 1
            if head == pending.count {
                pending.removeAll(keepingCapacity: true)
                head = 0
            } else if head >= 1024 && head >= pending.count / 2 {
                pending.removeFirst(head)
                head = 0
            }
            bufferedBytes -= data.count
            condition.broadcast()
            condition.unlock()
            continuation.resume(returning: data)
        } else if closed {
            condition.unlock()
            continuation.resume(returning: nil)
        } else {
            precondition(consumer == nil, "Binary input permits one consumer")
            consumer = continuation
            condition.unlock()
        }
    }

    func write(_ data: Data) throws {
        producer.lock()
        defer { producer.unlock() }
        let chunkSize = min(32768, capacity)
        for offset in stride(from: 0, to: data.count, by: chunkSize) {
            let chunk = data.subdata(in: offset..<min(offset + chunkSize, data.count))
            condition.lock()
            while !closed && (bufferedBytes > capacity - chunk.count || pending.count - head >= 8192) {
                condition.wait()
            }
            guard !closed else {
                condition.unlock()
                throw RuntimeError("Guest binary stream is closed")
            }
            let receiver = consumer
            consumer = nil
            if receiver == nil {
                pending.append(chunk)
                bufferedBytes += chunk.count
            }
            condition.unlock()
            receiver?.resume(returning: chunk)
        }
    }

    func close() { finish(discard: false) }
    func cancel() { finish(discard: true) }

    private func finish(discard: Bool) {
        condition.lock()
        closed = true
        if discard { pending.removeAll(); head = 0; bufferedBytes = 0 }
        let receiver = consumer
        consumer = nil
        condition.broadcast()
        condition.unlock()
        receiver?.resume(returning: nil)
    }
}
