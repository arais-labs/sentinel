import Foundation

/// Upgrade scripts, not a second database/schema system. Each script owns its
/// legacy interpretation; normal runtime operations never call these methods.
protocol RuntimeMigration: Sendable {
    var id: String { get }
    var metadataFiles: [String] { get }
    func needsInput(root: URL) throws -> Bool
    func validate(root: URL, input: [String: Any]) throws
    func apply(root: URL, input: [String: Any]) throws
    func verify(root: URL) throws
}

enum RuntimeMigrations {
    static let scripts: [any RuntimeMigration] = [WorkerOwnershipMigration(), VirtualDesktopMigration()]

    static func completed(root: URL) throws -> [String] {
        let value = try RuntimeUpdate.read(root.appendingPathComponent("migrations.json").path)
        if value is NSNull { return [] }
        guard let state = value as? [String: Any], let ids = state["completed"] as? [String],
              ids == Array(scripts.map(\.id).prefix(ids.count)), ids.count <= scripts.count else {
            throw RuntimeError("Runtime migration history is incompatible with this update")
        }
        return ids
    }

    static func status(root: URL) throws -> [String: Any] {
        let done = try completed(root: root)
        let pending = scripts.dropFirst(done.count)
        return ["completed": done, "target": scripts.map(\.id),
                "inputs": try pending.filter { try $0.needsInput(root: root) }.map(\.id)]
    }

    static func plan(root: URL, inputs: [String: Any]) throws {
        for script in scripts.dropFirst(try completed(root: root).count) {
            try script.validate(root: root, input: inputs[script.id] as? [String: Any] ?? [:])
        }
    }

    /// Caller holds both the update lease and exclusive service owner lock.
    /// Applying a script must be retry-safe: its effects can precede its marker.
    static func run(root: URL, inputs: [String: Any]) throws {
        try plan(root: root, inputs: inputs)
        var done = try completed(root: root)
        for script in scripts.dropFirst(done.count) {
            let input = inputs[script.id] as? [String: Any] ?? [:]
            let backups = root.appendingPathComponent("migration-backups")
            try FileManager.default.createDirectory(at: backups, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let backup = backups.appendingPathComponent(script.id + ".json")
            if !FileManager.default.fileExists(atPath: backup.path) {
                var metadata: [String: Any] = [:]
                for file in script.metadataFiles {
                    metadata[file] = try RuntimeUpdate.read(root.appendingPathComponent(file).path)
                }
                try RuntimeUpdate.write(["input": input, "metadata": metadata], to: backup.path)
            }
            try script.apply(root: root, input: input)
            try script.verify(root: root)
            done.append(script.id)
            try RuntimeUpdate.write(["completed": done], to: root.appendingPathComponent("migrations.json").path)
        }
    }

    static func checkActivation(root: URL, manifest: [String: Any]) throws {
        guard try completed(root: root) == (manifest["runtimeMigrations"] as? [String] ?? []) else {
            throw RuntimeError("This runtime cannot use the completed migrations; activation refused")
        }
    }
}
