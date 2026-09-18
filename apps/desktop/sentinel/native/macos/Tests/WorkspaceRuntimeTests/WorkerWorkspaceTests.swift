import Foundation
import Testing
@testable import WorkspaceRuntime

@MainActor struct WorkerWorkspaceTests {
    func spec(_ name: String = "Example") -> WorkerWorkspaceSpec {
        WorkerWorkspaceSpec(name: name, project: "/tmp/project", distribution: "ubuntu", tools: ["git"],
            resources: WorkerResources(), steps: [WorkerSetupStep(message: "Prepare", arguments: ["true"], timeout: 10)])
    }

    func directory() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("sentinel-worker-test-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    @Test func catalogSurvivesNewClientAndWorkerRestart() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let worker = try WorkerWorkspaces(root: root), id = UUID().uuidString.lowercased()
        let result = try worker.configure(Request(id: "configure", action: "workspace_configure", workspace: id, spec: spec(), revision: 0))
        let reopened = try WorkerWorkspaces(root: root)
        #expect(reopened.inventory("second-client").worker_id == result.worker_id)
        #expect(reopened.inventory("second-client").workspaces == result.workspaces)
        #expect(reopened.catalog.workspaces[id]?.revision == 1)
    }

    @Test func staleClientCannotOverwriteNewerSettings() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let worker = try WorkerWorkspaces(root: root), id = UUID().uuidString.lowercased()
        _ = try worker.configure(Request(id: "1", action: "workspace_configure", workspace: id, spec: spec(), revision: 0))
        _ = try worker.configure(Request(id: "2", action: "workspace_configure", workspace: id, spec: spec("Changed"), revision: 1))
        #expect(throws: RuntimeError.self) {
            try worker.configure(Request(id: "3", action: "workspace_configure", workspace: id, spec: spec("Stale"), revision: 1))
        }
        #expect(worker.catalog.workspaces[id]?.spec.name == "Changed")
    }

    @Test func startUsesStoredConfigurationAndSerializesClients() async throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let worker = try WorkerWorkspaces(root: root), id = UUID().uuidString.lowercased()
        _ = try worker.configure(Request(id: "1", action: "workspace_configure", workspace: id, spec: spec(), revision: 0))
        let started = AsyncStream<Void>.makeStream(), release = AsyncStream<Void>.makeStream(), finished = AsyncStream<Void>.makeStream()
        var calls: [Request] = []
        worker.attach { request in
            calls.append(request)
            if request.action == "start" {
                started.continuation.yield(())
                for await _ in release.stream { break }
            }
            if request.action == "exec" { finished.continuation.yield(()) }
            return Response(id: request.id, exitCode: 0)
        }
        _ = try worker.start(Request(id: "2", action: "workspace_start", workspace: id))
        for await _ in started.stream { break }
        #expect(throws: RuntimeError.self) { try worker.requireIdle() }
        #expect(throws: RuntimeError.self) { try worker.configure(Request(id: "3", action: "workspace_configure", workspace: id, spec: spec("Other"), revision: 1)) }
        #expect(calls.first?.project == "/tmp/project")
        #expect(calls.first?.distribution == "ubuntu")
        // No client object or transport is retained by the operation.
        release.continuation.yield(())
        for await _ in finished.stream { break }
        #expect(calls.map(\.action) == ["start", "exec"])
    }

    @Test func interruptedOperationIsNotReplayed() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        var catalog = WorkerWorkspaces.Catalog()
        let id = UUID().uuidString.lowercased()
        catalog.workspaces[id] = WorkerWorkspaceRecord(spec: spec(), revision: 4, operation: "preparing")
        try JSONEncoder().encode(catalog).write(to: root.appendingPathComponent("workspaces.json"))
        let worker = try WorkerWorkspaces(root: root)
        #expect(worker.catalog.workspaces[id]?.operation == nil)
        #expect(worker.catalog.workspaces[id]?.error?.contains("interrupted") == true)
        #expect(worker.catalog.workspaces[id]?.revision == 4)
    }

    @Test func updateResumesSeveralWorkspacesSeriallyWithoutAClient() async throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let worker = try WorkerWorkspaces(root: root)
        let ids = [UUID().uuidString.lowercased(), UUID().uuidString.lowercased()]
        for (index, id) in ids.enumerated() {
            _ = try worker.configure(Request(id: id, action: "workspace_configure", workspace: id, spec: spec("Workspace \(index)"), revision: 0))
        }
        let started = AsyncStream<Void>.makeStream(), release = AsyncStream<Void>.makeStream(), finished = AsyncStream<Void>.makeStream()
        var calls: [String] = []
        worker.attach { request in
            calls.append("\(request.action):\(request.workspace!)")
            if request.action == "start" && request.workspace == ids[0] {
                started.continuation.yield(())
                for await _ in release.stream { break }
            }
            if request.action == "exec" && request.workspace == ids[1] { finished.continuation.yield(()) }
            return Response(id: request.id, exitCode: 0)
        }
        _ = try worker.resume(Request(id: "resume", action: "workspace_resume", approved_workspaces: ids))
        for await _ in started.stream { break }
        #expect(throws: RuntimeError.self) { try worker.requireIdle() }
        #expect(calls == ["start:\(ids[0])"])
        release.continuation.yield(())
        for await _ in finished.stream { break }
        #expect(calls == ["start:\(ids[0])", "exec:\(ids[0])", "start:\(ids[1])", "exec:\(ids[1])"])
        #expect(worker.catalog.workspaces.values.allSatisfy { $0.operation == nil && $0.error == nil })
    }

    @Test func invalidCatalogIsNotSilentlyReplaced() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("workspaces.json")
        let corrupt = Data("not json".utf8)
        try corrupt.write(to: file)
        #expect(throws: (any Error).self) { try WorkerWorkspaces(root: root) }
        #expect(try Data(contentsOf: file) == corrupt)
    }

    @Test func existingDisksAreNeverHiddenByAnEmptyCatalog() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let diskDirectory = root.appendingPathComponent("store/containers/" + UUID().uuidString.lowercased())
        try FileManager.default.createDirectory(at: diskDirectory, withIntermediateDirectories: true)
        let disk = diskDirectory.appendingPathComponent("rootfs.ext4")
        try Data("keep".utf8).write(to: disk)
        #expect(throws: RuntimeError.self) { try WorkerWorkspaces(root: root) }
        #expect(!FileManager.default.fileExists(atPath: root.appendingPathComponent("workspaces.json").path))
        #expect(try Data(contentsOf: disk) == Data("keep".utf8))
    }

    @Test func diskShrinkAndDistributionChangesAreRejectedWithoutChangingCatalog() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let worker = try WorkerWorkspaces(root: root), id = UUID().uuidString.lowercased()
        _ = try worker.configure(Request(id: "1", action: "workspace_configure", workspace: id, spec: spec(), revision: 0))
        let before = try Data(contentsOf: root.appendingPathComponent("workspaces.json"))
        var changed = spec()
        changed.resources.disk_gib = 16
        #expect(throws: RuntimeError.self) { try worker.configure(Request(id: "2", action: "workspace_configure", workspace: id, spec: changed, revision: 1)) }
        changed = spec()
        changed.distribution = "alpine"
        #expect(throws: RuntimeError.self) { try worker.configure(Request(id: "3", action: "workspace_configure", workspace: id, spec: changed, revision: 1)) }
        #expect(try Data(contentsOf: root.appendingPathComponent("workspaces.json")) == before)
    }

    @Test func deleteRemovesOnlyTheSelectedRecordAfterNativeSuccess() async throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let worker = try WorkerWorkspaces(root: root), id = UUID().uuidString.lowercased(), other = UUID().uuidString.lowercased()
        _ = try worker.configure(Request(id: "1", action: "workspace_configure", workspace: id, spec: spec(), revision: 0))
        _ = try worker.configure(Request(id: "2", action: "workspace_configure", workspace: other, spec: spec("Other"), revision: 0))
        worker.attach { request in
            #expect(request.action == "delete")
            #expect(request.workspace == id)
            return Response(id: request.id)
        }
        _ = try await worker.stop(Request(id: "3", action: "workspace_delete", workspace: id), remove: true)
        #expect(worker.catalog.workspaces[id] == nil)
        #expect(worker.catalog.workspaces[other]?.spec.name == "Other")
        #expect(try WorkerWorkspaces(root: root).catalog.workspaces[id] == nil)
    }

    @Test func recoveryRestartsFromTheWorkerSpecAndRetainsTheBackup() async throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let worker = try WorkerWorkspaces(root: root), id = UUID().uuidString.lowercased()
        _ = try worker.configure(Request(id: "1", action: "workspace_configure", workspace: id, spec: spec(), revision: 0))
        var calls: [String] = []
        let finished = AsyncStream<Void>.makeStream()
        worker.attach { request in
            calls.append(request.action)
            if request.action == "exec" { finished.continuation.yield(()) }
            return Response(id: request.id, exitCode: 0, backup: request.action == "recover" ? "/backup" : nil)
        }
        _ = try worker.recover(Request(id: "2", action: "workspace_recover", workspace: id))
        for await _ in finished.stream { break }
        #expect(calls == ["recover", "start", "exec"])
        #expect(worker.catalog.workspaces[id]?.recovery_backup == "/backup")
        #expect(worker.catalog.workspaces[id]?.operation == nil)
    }
}
