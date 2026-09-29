import Foundation

/// Preserve existing desktops while moving selection out of development tools.
/// Guest packages/driver installation use the normal worker preparation contract.
struct VirtualDesktopMigration: RuntimeMigration {
    let id = "002_virtual_desktop"
    let metadataFiles = ["workspaces.json"]

    func needsInput(root: URL) throws -> Bool { false }
    func validate(root: URL, input: [String: Any]) throws {
        let file = root.appendingPathComponent("workspaces.json")
        // The preceding ownership migration creates a catalog on old workers.
        guard FileManager.default.fileExists(atPath: file.path) else { return }
        let catalog = try JSONDecoder().decode(WorkerCatalog.self, from: Data(contentsOf: file))
        guard catalog.schema == 1 else { throw RuntimeError("Unsupported workspace catalog") }
        for record in catalog.workspaces.values { try record.spec.validate() }
    }
    func apply(root: URL, input: [String: Any]) throws {
        let file = root.appendingPathComponent("workspaces.json")
        var catalog = try JSONDecoder().decode(WorkerCatalog.self, from: Data(contentsOf: file))
        for id in catalog.workspaces.keys {
            if catalog.workspaces[id]!.spec.tools.contains("desktop") {
                catalog.workspaces[id]!.spec.desktop = .xfce
                catalog.workspaces[id]!.spec.tools.removeAll { $0 == "desktop" }
            }
        }
        try RuntimeUpdate.write(try JSONSerialization.jsonObject(with: JSONEncoder().encode(catalog)), to: file.path)
    }
    func verify(root: URL) throws {
        let catalog = try JSONDecoder().decode(WorkerCatalog.self, from: Data(contentsOf: root.appendingPathComponent("workspaces.json")))
        guard catalog.workspaces.values.allSatisfy({ !$0.spec.tools.contains("desktop") }) else {
            throw RuntimeError("Desktop selection migration did not complete")
        }
    }
}
