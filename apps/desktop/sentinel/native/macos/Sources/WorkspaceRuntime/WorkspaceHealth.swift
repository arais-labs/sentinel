import Containerization
import Foundation
import Synchronization
@preconcurrency import Virtualization

/// Status never waits on guest RPCs. Process startup timeouts fail the request;
/// lifecycle/guest health failures retain the existing recovery safeguards.
final class WorkspaceHealth: Sendable {
    static let processStartupTimeout: Double = 60
    struct Snapshot {
        var states: [String: String] = [:]
        var errors: [String: String] = [:]
        var generations: [String: Int] = [:]
        var pending: [String: Int] = [:]
        var directory: URL?
    }
    @TaskLocal static var requestGeneration: Int?
    func generation(_ id: String) -> Int { snapshot.withLock { $0.generations[id, default: 0] } }
    private let snapshot = Mutex(Snapshot())
    func status(_ id: String) -> Response {
        snapshot.withLock { Response(id: id, event: "ready", states: $0.states,
            distributions: ["alpine", "ubuntu", "debian"], capabilities: ["workspace-recovery-v1", "workspace-reinstall-v1", "workspace-browser-v1"], errors: $0.errors) }
    }
    // A failed/interrupted repair must survive a runtime restart. Successful
    // recovery alone clears this marker; ordinary stopped VMs never get one.
    func restore(root: URL) throws {
        let directory = root.appendingPathComponent("recovery-required")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try snapshot.withLock { state in
            state.directory = directory
            for file in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) where UUID(uuidString: file.lastPathComponent) != nil {
                state.states[file.lastPathComponent] = "failed"
                state.errors[file.lastPathComponent] = (try? String(contentsOf: file, encoding: .utf8)) ?? "Recovery was interrupted. Choose Recover before starting this workspace."
            }
        }
    }
    func persistFault(_ id: String, _ message: String) {
        snapshot.withLock { state in
            if let directory = state.directory {
                do { try message.write(to: directory.appendingPathComponent(id), atomically: true, encoding: .utf8) }
                catch { state.errors[id] = message + " Recovery state could not be saved: \(error)" }
            }
        }
    }
    func completeRecovery(_ id: String) throws {
        try snapshot.withLock { state in
            if let directory = state.directory {
                let file = directory.appendingPathComponent(id)
                if FileManager.default.fileExists(atPath: file.path) { try FileManager.default.removeItem(at: file) }
            }
            state.states[id] = "stopped"
            state.errors.removeValue(forKey: id)
        }
    }
    func set(_ id: String, _ state: String) { snapshot.withLock { $0.states[id] = state; if state != "failed" { $0.errors.removeValue(forKey: id) } } }
    func remove(_ id: String) throws {
        try snapshot.withLock {
            if let directory = $0.directory {
                let marker = directory.appendingPathComponent(id)
                if FileManager.default.fileExists(atPath: marker.path) { try FileManager.default.removeItem(at: marker) }
            }
            // Late callbacks from the discarded VM must not poison its replacement.
            $0.generations[id, default: 0] += 1
            $0.states.removeValue(forKey: id)
            $0.errors.removeValue(forKey: id)
        }
    }
    func failed(_ id: String) -> Bool { snapshot.withLock { $0.errors[id] != nil } }
    func check(_ id: String, action: String = "exec") throws {
        if ["recover", "workspace_recover", "delete", "workspace_delete", "workspace_reinstall"].contains(action) { return }
        if let error = snapshot.withLock({ $0.errors[id] }) { throw RuntimeError(error) }
    }
    func fault(_ id: String, _ message: String) { snapshot.withLock { $0.states[id] = "failed"; $0.errors[id] = message }; persistFault(id, message) }
    func beginRecovery(_ id: String) throws {
        try snapshot.withLock {
            guard $0.errors[id] != nil, $0.states[id] != "recovering" else { throw RuntimeError("This workspace has not been identified as needing recovery") }
            $0.generations[id, default: 0] += 1
            $0.states[id] = "recovering"
        }
    }
    @MainActor func processStartup<T: Sendable>(_ id: String, seconds: Double = WorkspaceHealth.processStartupTimeout,
        operation: @escaping @MainActor () async throws -> T) async throws -> T {
        let generation = Self.requestGeneration ?? generation(id)
        guard generation == self.generation(id) else { throw CancellationError() }
        var timedOut = false
        do {
            let value = try await runtimeDeadline(seconds, onTimeout: {
                // The caller releases its request slot when the deadline returns.
                // Retain a slot for an RPC that ignores cancellation, including
                // its cleanup, until the underlying operation actually finishes.
                timedOut = true
                self.snapshot.withLock { $0.pending[id, default: 0] += 1 }
            }, onCompletion: {
                if timedOut { self.release(id) }
            }, operation: operation)
            guard generation == self.generation(id) else { throw CancellationError() }
            return value
        } catch is RuntimeDeadlineExceeded {
            throw RuntimeError("Process startup timed out after \(seconds.formatted()) seconds. The command may have started; check before retrying.")
        }
    }
    @MainActor func guest<T: Sendable>(_ id: String, seconds: Double = 15, operation: @escaping @MainActor () async throws -> T) async throws -> T {
        let generation = Self.requestGeneration ?? generation(id)
        guard generation == self.generation(id) else { throw CancellationError() }
        do {
            let value = try await runtimeDeadline(seconds, operation: operation)
            guard generation == self.generation(id) else { throw CancellationError() }
            return value
        }
        catch is RuntimeDeadlineExceeded {
            let current = snapshot.withLock {
                if $0.generations[id, default: 0] == generation {
                    $0.states[id] = "failed"
                    $0.errors[id] = RuntimeDeadlineExceeded().description
                    return true
                }
                return false
            }
            if current { persistFault(id, RuntimeDeadlineExceeded().description) }
            throw RuntimeDeadlineExceeded()
        }
    }
    func reserve(_ id: String) throws {
        try snapshot.withLock {
            guard $0.pending[id, default: 0] < 32 else { throw RuntimeError("Too many pending guest requests") }
            $0.pending[id, default: 0] += 1
        }
    }
    func release(_ id: String) { snapshot.withLock { $0.pending[id, default: 0] -= 1 } }
}

@MainActor
func powerOffWorkspace(_ machine: VZVirtualMachineInstance) async throws {
    try await runtimeDeadline(15) {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            machine.vmQueue.async {
                let vm = machine.vzVirtualMachine
                if vm.state == .stopped { continuation.resume(); return }
                vm.stop { error in
                    if let error { continuation.resume(throwing: error) }
                    else { continuation.resume() }
                }
            }
        }
    }
}
