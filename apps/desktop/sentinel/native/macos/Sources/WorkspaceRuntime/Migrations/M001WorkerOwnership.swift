import Foundation

struct WorkerOwnershipMigration: RuntimeMigration {
    let id = "001_worker_ownership"
    let metadataFiles = ["manifest.json", "workspaces.json"]

    private func diskIDs(_ root: URL) throws -> Set<String> {
        let path = root.appendingPathComponent("store/containers").path
        guard FileManager.default.fileExists(atPath: path) else { return [] }
        return Set(try FileManager.default.contentsOfDirectory(atPath: path).filter { UUID(uuidString: $0) != nil })
    }

    private func existing(_ root: URL) throws -> WorkerCatalog? {
        let file = root.appendingPathComponent("workspaces.json")
        guard FileManager.default.fileExists(atPath: file.path) else { return nil }
        let catalog = try JSONDecoder().decode(WorkerCatalog.self, from: Data(contentsOf: file))
        guard catalog.schema == 1, UUID(uuidString: catalog.worker_id) != nil else {
            throw RuntimeError("Invalid worker catalog; migration will not replace it")
        }
        for (id, record) in catalog.workspaces {
            guard UUID(uuidString: id) != nil, record.revision > 0 else { throw RuntimeError("Invalid workspace identity") }
            try record.spec.validate()
        }
        guard try diskIDs(root).isSubset(of: Set(catalog.workspaces.keys)) else {
            throw RuntimeError("Worker catalog is missing existing workspace disks")
        }
        return catalog
    }

    func needsInput(root: URL) throws -> Bool {
        try existing(root) == nil && (!diskIDs(root).isEmpty || FileManager.default.fileExists(atPath: root.appendingPathComponent("manifest.json").path))
    }

    private func records(root: URL, input: [String: Any]) throws -> [String: WorkerWorkspaceRecord] {
        if let current = try existing(root) { return current.workspaces }
        let disks = try diskIDs(root)
        guard let raw = input["workspaces"] else {
            if disks.isEmpty { return [:] }
            throw RuntimeError("Workspace migration needs registrations from the desktop that created these workspaces. Update from that desktop.")
        }
        let records = try JSONDecoder().decode([String: WorkerWorkspaceRecord].self, from: JSONSerialization.data(withJSONObject: raw))
        guard Set(records.keys) == disks else {
            throw RuntimeError("Workspace registrations and worker disks do not match. Reconcile the missing registrations before updating; no disks were changed.")
        }
        guard Set(records.values.map { $0.spec.name }).count == records.count else {
            throw RuntimeError("Workspace names conflict on this worker; rename the duplicates before updating")
        }
        for (id, record) in records {
            guard UUID(uuidString: id) != nil, record.revision == 1, record.operation == nil else {
                throw RuntimeError("Invalid or unfinished workspace registration")
            }
            try record.spec.validate()
            var directory: ObjCBool = false
            guard FileManager.default.fileExists(atPath: record.spec.project, isDirectory: &directory), directory.boolValue else {
                throw RuntimeError("Project folder is missing for \(record.spec.name); update not applied")
            }
            let container = root.appendingPathComponent("store/containers/" + id)
            let disk = container.appendingPathComponent("rootfs.ext4")
            let size = try disk.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            let allocation = record.spec.resources.disk_gib * 1024 * 1024 * 1024
            guard size > 0, UInt64(size) <= allocation, UInt64(size) == allocation || record.grow_disk else {
                throw RuntimeError("Disk allocation disagrees with the registration for \(record.spec.name)")
            }
            let distro = container.appendingPathComponent("distribution")
            let saved = FileManager.default.fileExists(atPath: distro.path) ? try String(contentsOf: distro, encoding: .utf8) : "alpine"
            guard saved == record.spec.distribution else { throw RuntimeError("Disk distribution disagrees with the registration for \(record.spec.name)") }
        }
        return records
    }

    func validate(root: URL, input: [String: Any]) throws { _ = try records(root: root, input: input) }

    func apply(root: URL, input: [String: Any]) throws {
        if try existing(root) != nil { return } // Retry after catalog write, before completion marker.
        var catalog = WorkerCatalog()
        catalog.workspaces = try records(root: root, input: input)
        let value = try JSONSerialization.jsonObject(with: JSONEncoder().encode(catalog))
        try RuntimeUpdate.write(value, to: root.appendingPathComponent("workspaces.json").path)
    }

    func verify(root: URL) throws {
        guard try existing(root) != nil else { throw RuntimeError("Worker catalog migration did not complete") }
    }
}
