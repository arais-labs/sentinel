import Foundation
import Testing
@testable import WorkspaceRuntime

@MainActor struct RecoveryTests {
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
    func uncooperative(_ delay: Double) async -> Int {
        await withCheckedContinuation { continuation in
            DispatchQueue.global().asyncAfter(deadline: .now() + delay) { continuation.resume(returning: 7) }
        }
    }
    @Test func deadlineReturnsBeforeUncooperativeOperationAndAcceptsLateCompletion() async throws {
        let start = ContinuousClock.now
        do {
            _ = try await runtimeDeadline(0.02) { await uncooperative(0.3) }
            Issue.record("Expected a timeout")
        } catch is RuntimeDeadlineExceeded {} catch { throw error }
        #expect(start.duration(to: .now) < .milliseconds(200))
        try await Task.sleep(for: .milliseconds(400))
        #expect(try await runtimeDeadline(1) { 42 } == 42)
    }
    @Test func onlyTimedOutWorkspaceNeedsRecoveryAndStatusIsImmediate() async throws {
        let health = WorkspaceHealth()
        health.set("stopped", "stopped")
        health.set("hung", "running")
        do { _ = try await health.guest("hung", seconds: 0.01) { await uncooperative(0.2) } }
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
        let request = Task { try await health.guest("vm", seconds: 0.1) { await uncooperative(0.3) } }
        try await Task.sleep(for: .milliseconds(20))
        health.fault("vm", "hung")
        try health.beginRecovery("vm")
        try health.completeRecovery("vm")
        health.set("vm", "running")
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
        var calls = 0
        do {
            _ = try await health.processStartup(id, seconds: 0.01) {
                calls += 1
                return await uncooperative(0.2)
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
        try await Task.sleep(for: .milliseconds(300))
        try health.reserve(id) // Late completion released exactly its own slot.
        #expect(throws: (any Error).self) { try health.reserve(id) }
        for _ in 0..<32 { health.release(id) }
        #expect(calls == 1)
        #expect(try await health.processStartup(id, seconds: 1) { 42 } == 42)
        #expect(health.status("probe").states?[id] == "running")
    }
    @Test func startupThatExceedsOldDeadlineCanFinishWithinNewBudget() async throws {
        let health = WorkspaceHealth()
        health.set("vm", "running")
        // Exercise the actual default budget against a startup beyond 15 seconds.
        #expect(try await health.processStartup("vm") { await uncooperative(15.2) } == 7)
        #expect(!health.failed("vm"))
    }
    @Test func recoveryFencesStartupCompletion() async throws {
        let health = WorkspaceHealth()
        health.set("vm", "running")
        let startup = Task { try await health.processStartup("vm", seconds: 1) { await uncooperative(0.1) } }
        try await Task.sleep(for: .milliseconds(20))
        health.fault("vm", "Explicit recovery")
        try health.beginRecovery("vm")
        try health.completeRecovery("vm")
        health.set("vm", "running")
        do {
            _ = try await startup.value
            Issue.record("Stale startup must not succeed")
        } catch is CancellationError {} catch { throw error }
        #expect(!health.failed("vm"))
    }
}
