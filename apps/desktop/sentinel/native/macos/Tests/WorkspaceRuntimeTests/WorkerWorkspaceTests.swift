import Foundation
import Testing
@testable import WorkspaceRuntime

@MainActor struct WorkerWorkspaceTests {
    @Test func workspaceQueueDoesNotBlockOtherWorkspacesAndKeepsFIFO() async {
        let queue = RuntimeRequestQueue()
        let started = AsyncStream<Void>.makeStream(), release = AsyncStream<Void>.makeStream()
        let independent = AsyncStream<Void>.makeStream()
        var events: [String] = []
        queue.submit(workspace: "a", action: "start") {
            events.append("a-start")
            started.continuation.yield(())
            for await _ in release.stream { break }
            events.append("a-ready")
        }
        for await _ in started.stream { break }
        queue.submit(workspace: "a", action: "stop") { events.append("a-stop") }
        queue.submit(workspace: "b", action: "stop") {
            events.append("b-stop")
            independent.continuation.yield(())
        }
        for await _ in independent.stream { break }
        #expect(events == ["a-start", "b-stop"])
        release.continuation.yield(())
        await queue.drain()
        #expect(events == ["a-start", "b-stop", "a-ready", "a-stop"])
    }

    @Test func stopCancelsGraphicsBeforeStoppingTheWorkspace() async {
        let queue = RuntimeRequestQueue(), started = AsyncStream<Void>.makeStream()
        let blocked = AsyncStream<Void>.makeStream()
        var events: [String] = []
        queue.submit(workspace: "a", action: "graphics_install") {
            started.continuation.yield(())
            for await _ in blocked.stream { break }
            #expect(Task.isCancelled)
            events.append("cleanup")
        }
        for await _ in started.stream { break }
        queue.submit(workspace: "a", action: "stop") { events.append("stop") }
        await queue.drain()
        #expect(events == ["cleanup", "stop"])
    }

    @Test func workspaceAndCompilerImagePinsAreIndependent() throws {
        let compiler = "docker.io/library/ubuntu@sha256:224a1869083a311ef3f13648a154ba79832fbef6364d31493642ca03082da254"
        try WorkspaceDistribution.validateImageReference(compiler)
        #expect(WorkspaceDistribution.ubuntu.command == ["/sbin/init"])
        #expect(WorkspaceDistribution.debian.command == ["/sbin/init"])
        #expect(WorkspaceDistribution.alpine.command == ["/sbin/openrc-init"])
        #expect(WorkspaceDistribution.ubuntu.buildCommand == ["/bin/sleep", "infinity"])
        for invalid in ["ubuntu:24.04", "ubuntu@sha256:123", "ubuntu @sha256:" + String(repeating: "a", count: 64)] {
            #expect(throws: RuntimeError.self) {
                try WorkspaceDistribution.validateImageReference(invalid)
            }
        }
    }

    @Test func desktopChoicesRoundTripWithoutRenamingSavedSelections() throws {
        for value in ["none", "xfce", "weston", "lxqt", "gnome", "plasma"] {
            let desktop = try JSONDecoder().decode(WorkspaceDesktop.self, from: Data("\"\(value)\"".utf8))
            #expect(desktop.rawValue == value)
            #expect(try JSONDecoder().decode(String.self, from: JSONEncoder().encode(desktop)) == value)
        }
        #expect(throws: DecodingError.self) {
            try JSONDecoder().decode(WorkspaceDesktop.self, from: Data("\"unknown\"".utf8))
        }
    }

    func spec(_ name: String = "Example") -> WorkerWorkspaceSpec {
        WorkerWorkspaceSpec(name: name, project: "/tmp/project", distribution: "ubuntu", tools: ["git"],
            resources: WorkerResources(), steps: [WorkerSetupStep(message: "Prepare", arguments: ["true"], timeout: 10)])
    }

    @Test func browserSelectionRoundTripsAndRejectsUnsupportedPackages() throws {
        var value = spec()
        for browser in ["chromium", "firefox", "chrome"] {
            value.browser = browser
            try value.validate()
            #expect(try JSONDecoder().decode(WorkerWorkspaceSpec.self, from: JSONEncoder().encode(value)).browser == browser)
        }
        value.distribution = "alpine"
        #expect(throws: RuntimeError.self) { try value.validate() }
        value.browser = "unknown"
        #expect(throws: RuntimeError.self) { try value.validate() }
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
            if request.action == "browser_graphics_install" { finished.continuation.yield(()) }
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
        #expect(calls.map(\.action) == ["start", "exec", "browser_graphics_install"])
        #expect(calls.last?.workspace == id)
        #expect(calls.last?.distribution == "ubuntu")
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

    @Test func reinstallRequiresConfirmationAndRebuildsOnlySelectedDisk() async throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let worker = try WorkerWorkspaces(root: root), id = UUID().uuidString.lowercased()
        _ = try worker.configure(Request(id: "1", action: "workspace_configure", workspace: id, spec: spec(), revision: 0))
        let original = worker.catalog.workspaces[id]!.spec
        var calls: [Request] = []
        let finished = AsyncStream<Void>.makeStream()
        worker.attach { request in
            calls.append(request)
            if request.action == "browser_graphics_install" { finished.continuation.yield(()) }
            return Response(id: request.id, exitCode: 0)
        }
        #expect(throws: RuntimeError.self) {
            try worker.start(Request(id: "no", action: "workspace_reinstall", workspace: id))
        }
        #expect(calls.isEmpty)
        _ = try worker.start(Request(id: "2", action: "workspace_reinstall", workspace: id, revision: 1, confirmed: true))
        for await _ in finished.stream { break }
        #expect(calls.map(\.action) == ["delete", "start", "exec", "browser_graphics_install"])
        #expect(calls.allSatisfy { $0.workspace == id })
        #expect(calls[0].project == nil)
        #expect(calls[1].project == original.project)
        #expect(calls[1].distribution == original.distribution)
        #expect(calls[1].grow_disk == false)
        #expect(worker.catalog.workspaces[id]?.spec == original)
        #expect(worker.catalog.workspaces[id]?.revision == 1)
    }

    @Test func reinstallDeletionFailureCannotStartOrReplayDeletionOnRetry() async throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let worker = try WorkerWorkspaces(root: root), id = UUID().uuidString.lowercased()
        _ = try worker.configure(Request(id: "1", action: "workspace_configure", workspace: id, spec: spec(), revision: 0))
        let deleted = AsyncStream<Void>.makeStream(), finished = AsyncStream<Void>.makeStream()
        var calls: [String] = []
        worker.attach { request in
            calls.append(request.action)
            if request.action == "delete" {
                deleted.continuation.yield(())
                throw RuntimeError("Disk deletion refused")
            }
            if request.action == "browser_graphics_install" { finished.continuation.yield(()) }
            return Response(id: request.id, exitCode: 0)
        }
        _ = try worker.start(Request(id: "2", action: "workspace_reinstall", workspace: id, confirmed: true))
        for await _ in deleted.stream { break }
        #expect(calls == ["delete"])
        #expect(worker.catalog.workspaces[id]?.error == "Disk deletion refused")
        _ = try worker.start(Request(id: "3", action: "workspace_start", workspace: id))
        for await _ in finished.stream { break }
        #expect(calls == ["delete", "start", "exec", "browser_graphics_install"])
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
            if request.action == "browser_graphics_install" && request.workspace == ids[1] { finished.continuation.yield(()) }
            return Response(id: request.id, exitCode: 0)
        }
        _ = try worker.resume(Request(id: "resume", action: "workspace_resume", approved_workspaces: ids))
        for await _ in started.stream { break }
        #expect(throws: RuntimeError.self) { try worker.requireIdle() }
        #expect(calls == ["start:\(ids[0])"])
        release.continuation.yield(())
        for await _ in finished.stream { break }
        #expect(calls == ["start:\(ids[0])", "exec:\(ids[0])", "browser_graphics_install:\(ids[0])",
                          "start:\(ids[1])", "exec:\(ids[1])", "browser_graphics_install:\(ids[1])"])
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
            if request.action == "browser_graphics_install" { finished.continuation.yield(()) }
            return Response(id: request.id, exitCode: 0, backup: request.action == "recover" ? "/backup" : nil)
        }
        _ = try worker.recover(Request(id: "2", action: "workspace_recover", workspace: id))
        for await _ in finished.stream { break }
        #expect(calls == ["recover", "start", "exec", "browser_graphics_install"])
        #expect(worker.catalog.workspaces[id]?.recovery_backup == "/backup")
        #expect(worker.catalog.workspaces[id]?.operation == nil)
    }

    @Test(arguments: ["ubuntu", "debian", "alpine"], [WorkspaceDesktop.none, .xfce])
    func browserGraphicsProviderIsUbuntuSpecificAndIndependentOfDesktop(distribution: String, desktop: WorkspaceDesktop) async throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let worker = try WorkerWorkspaces(root: root), id = UUID().uuidString.lowercased()
        var selection = spec()
        selection.distribution = distribution
        selection.desktop = desktop
        selection.browser = "firefox" // Chromium's provider is still required on Ubuntu.
        _ = try worker.configure(Request(id: "configure", action: "workspace_configure", workspace: id, spec: selection, revision: 0))
        var expected = ["start", "exec"]
        if distribution == "ubuntu" { expected.append("browser_graphics_install") }
        if desktop != .none { expected.append("graphics_install") }
        let finalAction = expected.last!, finished = AsyncStream<Void>.makeStream()
        var calls: [Request] = []
        worker.attach { request in
            calls.append(request)
            if request.action == finalAction { finished.continuation.yield(()) }
            return Response(id: request.id, exitCode: 0)
        }
        _ = try worker.start(Request(id: "start", action: "workspace_start", workspace: id))
        for await _ in finished.stream { break }
        #expect(calls.map(\.action) == expected)
        #expect(calls.allSatisfy { $0.workspace == id })
        for request in calls where request.action.hasSuffix("graphics_install") {
            #expect(request.distribution == distribution)
        }
        if desktop != .none { #expect(calls.last?.desktop == desktop) }
        #expect(worker.catalog.workspaces[id]?.operation == nil)
        #expect(worker.catalog.workspaces[id]?.error == nil)
    }

    @Test func browserGraphicsFailurePreventsDesktopInstallationAndIsRecorded() async throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let worker = try WorkerWorkspaces(root: root), id = UUID().uuidString.lowercased()
        var selection = spec()
        selection.desktop = .xfce
        _ = try worker.configure(Request(id: "configure", action: "workspace_configure", workspace: id, spec: selection, revision: 0))
        let failed = AsyncStream<Void>.makeStream()
        var calls: [String] = []
        worker.attach { request in
            calls.append(request.action)
            if request.action == "browser_graphics_install" {
                failed.continuation.yield(())
                throw RuntimeError("Browser graphics provider unavailable")
            }
            return Response(id: request.id, exitCode: 0)
        }
        _ = try worker.start(Request(id: "start", action: "workspace_start", workspace: id))
        for await _ in failed.stream { break }
        #expect(calls == ["start", "exec", "browser_graphics_install"])
        #expect(worker.catalog.workspaces[id]?.operation == nil)
        #expect(worker.catalog.workspaces[id]?.error == "Browser graphics provider unavailable")
        try worker.requireIdle()
    }
}
