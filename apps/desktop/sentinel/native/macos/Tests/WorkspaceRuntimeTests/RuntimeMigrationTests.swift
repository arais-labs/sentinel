import Foundation
import Testing
@testable import WorkspaceRuntime

struct RuntimeMigrationTests {
    func directory() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("sentinel-migrations-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    func legacy(_ root: URL) throws -> (String, [String: Any]) {
        let id = UUID().uuidString.lowercased()
        let folder = root.appendingPathComponent("store/containers/" + id)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let disk = folder.appendingPathComponent("rootfs.ext4")
        try Data("preserve disk".utf8).write(to: disk)
        let file = try FileHandle(forWritingTo: disk)
        try file.truncate(atOffset: 8 * 1024 * 1024 * 1024)
        try file.close()
        let spec = WorkerWorkspaceSpec(name: "Project", project: root.path, distribution: "alpine", tools: ["git"],
            resources: WorkerResources(cpus: 2, memory_gib: 2, disk_gib: 8),
            steps: [WorkerSetupStep(message: "Prepare", arguments: ["true"], timeout: 10)])
        let record = WorkerWorkspaceRecord(spec: spec, revision: 1)
        let records = try JSONSerialization.jsonObject(with: JSONEncoder().encode([id: record]))
        return (id, ["001_worker_ownership": ["workspaces": records]])
    }

    @Test func freshInstallAndRepeatedUpdateNeedNoLegacyClient() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        #expect(try RuntimeMigrations.status(root: root)["inputs"] as? [String] == [])
        try RuntimeMigrations.run(root: root, inputs: [:])
        let catalog = try Data(contentsOf: root.appendingPathComponent("workspaces.json"))
        try RuntimeMigrations.run(root: root, inputs: [:])
        #expect(try Data(contentsOf: root.appendingPathComponent("workspaces.json")) == catalog)
        #expect(try RuntimeMigrations.completed(root: root) == ["001_worker_ownership", "002_virtual_desktop"])
    }

    @Test func legacyTransferPreservesDiskAndOriginalMetadataAndSkipsAfterCompletion() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let (id, inputs) = try legacy(root)
        try RuntimeUpdate.write(["version": "old"], to: root.appendingPathComponent("manifest.json").path)
        #expect(try RuntimeMigrations.status(root: root)["inputs"] as? [String] == ["001_worker_ownership"])
        try RuntimeMigrations.plan(root: root, inputs: inputs)
        #expect(!FileManager.default.fileExists(atPath: root.appendingPathComponent("workspaces.json").path))
        try RuntimeMigrations.run(root: root, inputs: inputs)
        let data = try Data(contentsOf: root.appendingPathComponent("workspaces.json"))
        let catalog = try JSONDecoder().decode(WorkerCatalog.self, from: data)
        #expect(catalog.workspaces[id]?.spec.resources.disk_gib == 8)
        #expect(catalog.workspaces[id]?.spec.project == root.path)
        let disk = try FileHandle(forReadingFrom: root.appendingPathComponent("store/containers/" + id + "/rootfs.ext4"))
        defer { try? disk.close() }
        #expect(try disk.read(upToCount: 13) == Data("preserve disk".utf8))
        let backup = try RuntimeUpdate.read(root.appendingPathComponent("migration-backups/001_worker_ownership.json").path) as? [String: Any]
        #expect((backup?["metadata"] as? [String: Any])?["manifest.json"] as? [String: String] == ["version": "old"])
        try RuntimeMigrations.run(root: root, inputs: [:])
        #expect(try Data(contentsOf: root.appendingPathComponent("workspaces.json")) == data)
    }

    @Test func missingAndConflictingInputsFailBeforeAnyWrites() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let (_, inputs) = try legacy(root)
        #expect(throws: RuntimeError.self) { try RuntimeMigrations.plan(root: root, inputs: [:]) }
        #expect(throws: RuntimeError.self) { try RuntimeMigrations.run(root: root, inputs: ["001_worker_ownership": ["workspaces": [:]]]) }
        let diskDirectories = root.appendingPathComponent("store/containers")
        let id = try FileManager.default.contentsOfDirectory(atPath: diskDirectories.path)[0]
        try "ubuntu".write(to: diskDirectories.appendingPathComponent(id + "/distribution"), atomically: true, encoding: .utf8)
        #expect(throws: RuntimeError.self) { try RuntimeMigrations.plan(root: root, inputs: inputs) }
        #expect(!FileManager.default.fileExists(atPath: root.appendingPathComponent("workspaces.json").path))
        #expect(!FileManager.default.fileExists(atPath: root.appendingPathComponent("migrations.json").path))
        #expect(!FileManager.default.fileExists(atPath: root.appendingPathComponent("migration-backups").path))
    }

    @Test func interruptedCompletionKeepsCatalogIdentityAndRejectsOldRuntimeActivation() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let (_, inputs) = try legacy(root)
        // Crash boundary: script committed its catalog but not its completion marker.
        try WorkerOwnershipMigration().apply(root: root, input: inputs["001_worker_ownership"] as! [String: Any])
        let before = try Data(contentsOf: root.appendingPathComponent("workspaces.json"))
        try RuntimeMigrations.run(root: root, inputs: [:])
        #expect(try Data(contentsOf: root.appendingPathComponent("workspaces.json")) == before)
        #expect(throws: RuntimeError.self) { try RuntimeMigrations.checkActivation(root: root, manifest: [:]) }
        #expect(throws: RuntimeError.self) { try RuntimeMigrations.checkActivation(root: root, manifest: ["runtimeMigrations": ["001_worker_ownership"]]) }
        try RuntimeMigrations.checkActivation(root: root, manifest: ["runtimeMigrations": ["001_worker_ownership", "002_virtual_desktop"]])
    }

    @Test func unknownMigrationHistoryCannotBeDowngraded() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("migrations.json")
        try RuntimeUpdate.write(["completed": ["001_worker_ownership", "future"]], to: file.path)
        let before = try Data(contentsOf: file)
        #expect(throws: RuntimeError.self) { try RuntimeMigrations.run(root: root, inputs: [:]) }
        #expect(try Data(contentsOf: file) == before)
    }

    @Test func virtualDesktopMigrationConvertsLegacySelectionWithoutChangingWorkspaceIdentity() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let (id, inputs) = try legacy(root)
        try WorkerOwnershipMigration().apply(root: root, input: inputs["001_worker_ownership"] as! [String: Any])
        let file = root.appendingPathComponent("workspaces.json")
        var catalog = try JSONDecoder().decode(WorkerCatalog.self, from: Data(contentsOf: file))
        let owner = catalog.worker_id
        catalog.workspaces[id]!.spec.tools = ["git", "desktop", "python"]
        catalog.workspaces[id]!.revision = 7
        let before = catalog.workspaces[id]!
        try RuntimeUpdate.write(try JSONSerialization.jsonObject(with: JSONEncoder().encode(catalog)), to: file.path)
        let migration = VirtualDesktopMigration()
        try migration.validate(root: root, input: [:])
        try migration.apply(root: root, input: [:])
        try migration.verify(root: root)
        let migrated = try JSONDecoder().decode(WorkerCatalog.self, from: Data(contentsOf: file))
        #expect(migrated.worker_id == owner)
        var expected = before
        expected.spec.tools = ["git", "python"]
        expected.spec.desktop = .xfce
        #expect(migrated.workspaces[id] == expected)
        let saved = try Data(contentsOf: file)
        try migration.apply(root: root, input: [:])
        #expect(try Data(contentsOf: file) == saved)
    }
}
