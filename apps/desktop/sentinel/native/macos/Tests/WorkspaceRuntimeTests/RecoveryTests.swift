import Foundation
import Testing
@testable import WorkspaceRuntime

@MainActor struct RecoveryTests {
    // Unlike a timed sleep, this operation cannot finish until the test releases
    // it, and deliberately ignores cancellation just like a stuck guest RPC.
    @MainActor private final class OperationGate {
        var started = false
        private var released = false
        private var continuation: CheckedContinuation<Int, Never>?
        func run() async -> Int {
            started = true
            return await withCheckedContinuation {
                if released { $0.resume(returning: 7) }
                else { continuation = $0 }
            }
        }
        func release() {
            released = true
            continuation?.resume(returning: 7)
            continuation = nil
        }
    }

    // Yield to the work being observed, never assume a sleep means it completed.
    // The deadline bounds failures; it is not part of the successful test path.
    private func eventually(_ condition: () -> Bool) async throws {
        let deadline = ContinuousClock.now + .seconds(5)
        while !condition() {
            try #require(ContinuousClock.now < deadline, "Expected operation did not complete")
            await Task.yield()
        }
    }
    @Test func failedWorkspaceAllowsDestructiveExitAndDeletionClearsPersistentFault() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let id = UUID().uuidString
        let health = WorkspaceHealth()
        try health.restore(root: root)
        health.fault(id, "Repair cannot complete")
        for action in ["delete", "workspace_delete", "workspace_reinstall", "recover", "workspace_recover"] {
            try health.check(id, action: action)
        }
        for action in ["start", "workspace_start", "exec"] {
            #expect(throws: (any Error).self) { try health.check(id, action: action) }
        }
        let generation = health.generation(id)
        try health.remove(id)
        #expect(health.generation(id) > generation)
        let restored = WorkspaceHealth()
        try restored.restore(root: root)
        #expect(!restored.failed(id))
        #expect(restored.status("probe").states?[id] == nil)
    }
    @Test func deadlineReturnsBeforeUncooperativeOperationAndAcceptsLateCompletion() async throws {
        let operation = OperationGate()
        defer { operation.release() }
        var completed = false
        do {
            _ = try await runtimeDeadline(0.01, onCompletion: { completed = true }) {
                await operation.run()
            }
            Issue.record("Expected a timeout")
        } catch is RuntimeDeadlineExceeded {} catch { throw error }
        #expect(!completed)
        operation.release()
        try await eventually { completed }
        #expect(try await runtimeDeadline(1) { 42 } == 42)
    }
    @Test func onlyTimedOutWorkspaceNeedsRecoveryAndStatusIsImmediate() async throws {
        let health = WorkspaceHealth()
        health.set("stopped", "stopped")
        health.set("hung", "running")
        let operation = OperationGate()
        defer { operation.release() }
        do {
            _ = try await health.guest("hung", seconds: 0.01) { await operation.run() }
            Issue.record("Expected a guest timeout")
        }
        catch is RuntimeDeadlineExceeded {} catch { throw error }
        let status = health.status("probe")
        #expect(status.states?["stopped"] == "stopped")
        #expect(status.errors?["stopped"] == nil)
        #expect(status.states?["hung"] == "failed")
        #expect(throws: (any Error).self) { try health.beginRecovery("stopped") }
        try health.beginRecovery("hung")
        #expect(health.status("probe").states?["hung"] == "recovering")
        #expect(throws: (any Error).self) { try health.beginRecovery("hung") }
    }
    @Test func oldGuestDeadlineCannotPoisonRecoveredWorkspace() async throws {
        let health = WorkspaceHealth()
        health.set("vm", "running")
        let operation = OperationGate()
        defer { operation.release() }
        let request = Task {
            try await health.guest("vm", seconds: 60) { () async throws -> Int in
                _ = await operation.run()
                throw RuntimeDeadlineExceeded()
            }
        }
        try await eventually { operation.started }
        health.fault("vm", "hung")
        try health.beginRecovery("vm")
        try health.completeRecovery("vm")
        health.set("vm", "running")
        operation.release()
        _ = await request.result
        #expect(health.status("probe").states?["vm"] == "running")
        #expect(!health.failed("vm"))
    }
    @Test func recoveryRequirementSurvivesRestartAndClearsOnlyAfterCompletion() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let id = UUID().uuidString
        let health = WorkspaceHealth()
        try health.restore(root: root)
        health.fault(id, "Guest did not respond")
        try health.beginRecovery(id)
        let restored = WorkspaceHealth()
        try restored.restore(root: root)
        #expect(restored.failed(id))
        try restored.beginRecovery(id)
        try restored.completeRecovery(id)
        let again = WorkspaceHealth()
        try again.restore(root: root)
        #expect(!again.failed(id))
    }
    @Test func startupTimeoutKeepsWorkspaceUsableAndAccountsForLateWork() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let id = UUID().uuidString
        let health = WorkspaceHealth()
        try health.restore(root: root)
        health.set(id, "running")
        try health.reserve(id)
        let operation = OperationGate()
        defer { operation.release() }
        var calls = 0
        do {
            _ = try await health.processStartup(id, seconds: 0.01) {
                calls += 1
                return await operation.run()
            }
            Issue.record("Expected a startup timeout")
        } catch {
            #expect(String(describing: error).contains("Process startup timed out"))
            #expect(!String(describing: error).contains("Recover"))
        }
        health.release(id) // Caller has returned, but the RPC is still pending.
        #expect(health.status("probe").states?[id] == "running")
        #expect(!health.failed(id))
        try health.check(id)
        let restored = WorkspaceHealth()
        try restored.restore(root: root)
        #expect(!restored.failed(id))
        for _ in 0..<31 { try health.reserve(id) }
        #expect(throws: (any Error).self) { try health.reserve(id) }
        operation.release()
        // A successful reservation is the observable acknowledgement that the
        // deadline wrapper's completion cleanup (not just the RPC) has run.
        try await eventually { (try? health.reserve(id)) != nil }
        #expect(throws: (any Error).self) { try health.reserve(id) }
        for _ in 0..<32 { health.release(id) }
        #expect(calls == 1)
        #expect(try await health.processStartup(id, seconds: 1) { 42 } == 42)
        #expect(health.status("probe").states?[id] == "running")
    }
    @Test func startupUsesExtendedDefaultBudgetAndAcceptsCompletion() async throws {
        let health = WorkspaceHealth()
        health.set("vm", "running")
        #expect(WorkspaceHealth.processStartupTimeout == 60)
        #expect(try await health.processStartup("vm") { 7 } == 7)
        #expect(!health.failed("vm"))
    }
    @Test func recoveryFencesStartupCompletion() async throws {
        let health = WorkspaceHealth()
        health.set("vm", "running")
        let operation = OperationGate()
        defer { operation.release() }
        let startup = Task { try await health.processStartup("vm") { await operation.run() } }
        try await eventually { operation.started }
        health.fault("vm", "Explicit recovery")
        try health.beginRecovery("vm")
        try health.completeRecovery("vm")
        health.set("vm", "running")
        operation.release()
        do {
            _ = try await startup.value
            Issue.record("Stale startup must not succeed")
        } catch is CancellationError {} catch { throw error }
        #expect(!health.failed("vm"))
    }
}
