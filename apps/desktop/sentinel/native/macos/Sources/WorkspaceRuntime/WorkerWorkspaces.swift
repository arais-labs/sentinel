import Foundation

/// One authoritative catalog per worker/account. The service's owner lock
/// protects this file; clients cannot create their own competing registries.
@MainActor final class WorkerWorkspaces {
    typealias Catalog = WorkerCatalog
    typealias Perform = @MainActor (Request) async throws -> Response
    private let file: URL
    private(set) var catalog: Catalog
    private var jobs: [String: Task<Void, Never>] = [:]
    private var perform: Perform?

    init(root: URL) throws {
        file = root.appendingPathComponent("workspaces.json")
        if FileManager.default.fileExists(atPath: file.path) {
            catalog = try JSONDecoder().decode(Catalog.self, from: Data(contentsOf: file))
            guard catalog.schema == 1, UUID(uuidString: catalog.worker_id) != nil else {
                throw RuntimeError("Unsupported worker workspace catalog")
            }
            for (id, record) in catalog.workspaces {
                guard UUID(uuidString: id) != nil else { throw RuntimeError("Invalid workspace ID") }
                try record.spec.validate()
            }
            // Never replay a possibly executed operation after a worker crash.
            for id in catalog.workspaces.keys where catalog.workspaces[id]?.operation != nil {
                catalog.workspaces[id]?.operation = nil
                catalog.workspaces[id]?.error = "Workspace operation was interrupted. Check the workspace before retrying."
            }
        } else {
            let disks = root.appendingPathComponent("store/containers")
            if FileManager.default.fileExists(atPath: disks.path),
               try FileManager.default.contentsOfDirectory(atPath: disks.path).contains(where: { UUID(uuidString: $0) != nil }) {
                throw RuntimeError("Worker catalog is missing for existing workspace disks. Restore the catalog before starting this runtime.")
            }
            catalog = Catalog()
        }
        try save(catalog)
    }

    func attach(_ perform: @escaping Perform) { self.perform = perform }

    private func save(_ next: Catalog) throws {
        let value = try JSONSerialization.jsonObject(with: JSONEncoder().encode(next))
        try RuntimeUpdate.write(value, to: file.path)
        catalog = next
    }

    func inventory(_ id: String) -> Response {
        Response(id: id, worker_id: catalog.worker_id, workspaces: catalog.workspaces)
    }

    func requireIdle() throws {
        // ContainerManager mutations include async, inout disk recovery. Keep
        // lifecycle changes serial; guest processes and desktop streams remain independent.
        guard jobs.isEmpty else { throw RuntimeError("Wait for the current worker operation to finish") }
    }

    private func record(_ id: String) throws -> WorkerWorkspaceRecord {
        guard let value = catalog.workspaces[id] else { throw RuntimeError("Workspace not found on this worker") }
        return value
    }

    func configure(_ request: Request) throws -> Response {
        guard let id = request.workspace, UUID(uuidString: id) != nil, let spec = request.spec else {
            throw RuntimeError("Workspace ID and configuration are required")
        }
        try requireIdle()
        try spec.validate()
        let previous = catalog.workspaces[id]
        guard request.revision == (previous?.revision ?? 0) else {
            throw RuntimeError("Workspace settings changed. Refresh before editing again.")
        }
        guard !catalog.workspaces.contains(where: { $0.key != id && $0.value.spec.name == spec.name }) else {
            throw RuntimeError("A workspace with this name already exists on this worker")
        }
        if let previous {
            guard previous.spec.distribution == spec.distribution else { throw RuntimeError("Existing workspace disks cannot change distribution") }
            guard spec.resources.disk_gib >= previous.spec.resources.disk_gib else { throw RuntimeError("Workspace disks can only be increased") }
            guard Set(previous.spec.tools).isSubset(of: Set(spec.tools)) else { throw RuntimeError("Installed workspace tools are kept; choose additional tools") }
        }
        var next = catalog
        next.workspaces[id] = WorkerWorkspaceRecord(spec: spec, revision: (previous?.revision ?? 0) + 1,
            error: previous?.error,
            recovery_backup: previous?.recovery_backup,
            reconfigure: previous?.reconfigure == true || (previous != nil && (previous!.spec.project != spec.project || previous!.spec.resources != spec.resources)),
            grow_disk: previous?.grow_disk == true || (previous != nil && previous!.spec.resources.disk_gib < spec.resources.disk_gib))
        try save(next)
        return inventory(request.id)
    }

    func start(_ request: Request) throws -> Response {
        guard let id = request.workspace else { throw RuntimeError("Workspace ID is required") }
        try requireIdle()
        let entry = try record(id)
        if let revision = request.revision, revision != entry.revision {
            throw RuntimeError("Workspace settings changed. Refresh before retrying.")
        }
        var plan = entry.spec
        if let steps = request.steps { plan.steps = steps; try plan.validate() }
        guard let perform else { throw RuntimeError("Worker is not ready") }
        var next = catalog
        next.workspaces[id]?.operation = "preparing"
        next.workspaces[id]?.error = nil
        try save(next)
        jobs[id] = Task { await self.prepare(id, steps: plan.steps, perform: perform) }
        return Response(id: request.id, event: "preparing")
    }

    private func prepare(_ id: String, steps: [WorkerSetupStep], perform: Perform) async {
        do {
            try Task.checkCancellation()
            let entry = try record(id), spec = entry.spec
            if entry.reconfigure { _ = try await perform(Request(id: UUID().uuidString, action: "stop", workspace: id)) }
            _ = try await perform(Request(id: UUID().uuidString, action: "start", workspace: id,
                project: spec.project, distribution: spec.distribution, cpus: spec.resources.cpus,
                memory_gib: spec.resources.memory_gib, disk_gib: spec.resources.disk_gib, grow_disk: entry.grow_disk))
            for step in steps {
                try Task.checkCancellation()
                let result = try await perform(Request(id: UUID().uuidString, action: "exec", workspace: id,
                    arguments: step.arguments, timeout: step.timeout))
                guard result.exitCode == 0 else { throw RuntimeError("Workspace setup failed at \(step.message): \(result.stderr ?? result.stdout ?? "")") }
            }
            if spec.tools.contains("desktop") {
                try Task.checkCancellation()
                _ = try await perform(Request(id: UUID().uuidString, action: "graphics_install", workspace: id, distribution: spec.distribution))
            }
            var next = catalog
            try Task.checkCancellation()
            next.workspaces[id]?.operation = nil
            next.workspaces[id]?.reconfigure = false
            next.workspaces[id]?.grow_disk = false
            try save(next)
        } catch { finishFailure(id, error) }
        jobs.removeValue(forKey: id)
    }

    /// Resume an approved update snapshot as one worker-owned operation. The
    /// client can disconnect immediately; ContainerManager mutations stay serial.
    func resume(_ request: Request) throws -> Response {
        try requireIdle()
        guard let ids = request.approved_workspaces, Set(ids).count == ids.count,
              let perform else { throw RuntimeError("Approved workspace IDs are required") }
        for id in ids { _ = try record(id) }
        var next = catalog
        for id in ids {
            next.workspaces[id]?.operation = "preparing"
            next.workspaces[id]?.error = nil
        }
        try save(next)
        jobs["resume"] = Task {
            for id in ids {
                await self.prepare(id, steps: self.catalog.workspaces[id]!.spec.steps, perform: perform)
            }
            self.jobs.removeValue(forKey: "resume")
        }
        return Response(id: request.id, event: "preparing")
    }

    private func finishFailure(_ id: String, _ error: Error) {
        var next = catalog
        next.workspaces[id]?.operation = nil
        next.workspaces[id]?.error = String(describing: error)
        do { try save(next) }
        catch { catalog = next; catalog.workspaces[id]?.error = "Could not persist workspace result: \(error)" }
    }

    func stop(_ request: Request, remove: Bool = false) async throws -> Response {
        guard let id = request.workspace, let perform else { throw RuntimeError("Workspace ID is required") }
        if let job = jobs[id], catalog.workspaces[id]?.operation == "preparing" {
            var next = catalog
            next.workspaces[id]?.operation = "stopping"
            try save(next)
            job.cancel()
            await job.value
        }
        try requireIdle()
        _ = try record(id)
        var next = catalog
        next.workspaces[id]?.operation = "stopping"
        try save(next)
        // Mark busy before yielding, including requests from a second client.
        let task = Task { @MainActor in
            do {
                _ = try await perform(Request(id: UUID().uuidString, action: remove ? "delete" : "stop", workspace: id))
                var finished = self.catalog
                if remove { finished.workspaces.removeValue(forKey: id) }
                else { finished.workspaces[id]?.operation = nil; finished.workspaces[id]?.error = nil }
                try self.save(finished)
            } catch { self.finishFailure(id, error) }
            self.jobs.removeValue(forKey: id)
        }
        jobs[id] = task
        await task.value
        if let error = catalog.workspaces[id]?.error { throw RuntimeError(error) }
        return Response(id: request.id)
    }

    func recover(_ request: Request) throws -> Response {
        guard let id = request.workspace, let perform else { throw RuntimeError("Workspace ID is required") }
        try requireIdle()
        _ = try record(id)
        var next = catalog
        next.workspaces[id]?.operation = "recovering"
        next.workspaces[id]?.error = nil
        try save(next)
        jobs[id] = Task {
            do {
                let result = try await perform(Request(id: UUID().uuidString, action: "recover", workspace: id))
                var next = self.catalog
                next.workspaces[id]?.operation = "preparing"
                next.workspaces[id]?.recovery_backup = result.backup
                try self.save(next)
                await self.prepare(id, steps: self.catalog.workspaces[id]!.spec.steps, perform: perform)
            } catch { self.finishFailure(id, error) }
            self.jobs.removeValue(forKey: id)
        }
        return Response(id: request.id, event: "recovering")
    }
}
