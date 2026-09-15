import Foundation

struct RuntimeDeadlineExceeded: Error, CustomStringConvertible {
    var description: String { "Guest did not respond. Open Workspaces and choose Recover." }
}

/// A task-group timeout waits for uncooperative RPC children before returning.
/// This latch returns at the deadline; the operation remains accounted for by
/// its owner until it actually finishes. Recovery must fence those operations.
@MainActor
private final class DeadlineResult<T: Sendable> {
    var continuation: CheckedContinuation<T, Error>?
    init(_ continuation: CheckedContinuation<T, Error>) { self.continuation = continuation }
    @discardableResult func finish(_ result: Result<T, Error>) -> Bool {
        guard let continuation else { return false }
        self.continuation = nil
        continuation.resume(with: result)
        return true
    }
}

@MainActor
func runtimeDeadline<T: Sendable>(_ seconds: Double,
    onTimeout: @escaping @MainActor () -> Void = {},
    onCompletion: @escaping @MainActor () -> Void = {},
    operation: @escaping @MainActor () async throws -> T) async throws -> T {
    try await withCheckedThrowingContinuation { continuation in
        let result = DeadlineResult(continuation)
        var timer: Task<Void, Never>?
        let work = Task {
            defer { onCompletion() }
            do { result.finish(.success(try await operation())) }
            catch { result.finish(.failure(error)) }
            timer?.cancel()
        }
        timer = Task {
            do { try await Task.sleep(for: .seconds(seconds)) } catch { return }
            if result.finish(.failure(RuntimeDeadlineExceeded())) {
                onTimeout()
                work.cancel()
            }
        }
    }
}
