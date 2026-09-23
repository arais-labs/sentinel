import Foundation

/// Lifecycle requests are FIFO within a workspace, independent across workspaces.
/// Tasks stay owned until completion so maintenance/EOF can drain before teardown.
@MainActor final class RuntimeRequestQueue {
    private var tails: [String: Task<Void, Never>] = [:]
    private var tasks: [UUID: (String, String, Task<Void, Never>)] = [:]

    func submit(workspace: String, action: String, operation: @escaping @MainActor () async -> Void) {
        // An explicit stop cancels graphics setup, then waits for its cleanup.
        // Disk creation/recovery are not interrupted halfway through mutation.
        if ["stop", "delete"].contains(action) {
            for (id, kind, task) in tasks.values where id == workspace && ["graphics_install", "browser_graphics_install"].contains(kind) {
                task.cancel()
            }
        }
        let previous = tails[workspace]
        let token = UUID()
        let task = Task { @MainActor in
            await previous?.value
            await operation()
            self.tasks.removeValue(forKey: token)
            if !self.tasks.values.contains(where: { $0.0 == workspace }) {
                self.tails.removeValue(forKey: workspace)
            }
        }
        tasks[token] = (workspace, action, task)
        tails[workspace] = task
    }

    func drain() async {
        for task in Array(tails.values) { await task.value }
    }
}

/// MainActor alone does not protect an async inout borrow of ContainerManager.
/// Keep SDK disk/network mutations exclusive, not guest package installation.
@MainActor final class RuntimeMutationGate {
    private var held = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func acquire() async {
        if !held { held = true; return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func release() {
        if waiters.isEmpty { held = false }
        else { waiters.removeFirst().resume() }
    }
}
