import Foundation

struct WorkerResources: Codable, Equatable, Sendable {
    var cpus: Int = 2
    var memory_gib: UInt64 = 2
    var disk_gib: UInt64 = 32
}

/// A setup plan is supplied with an explicit configuration change, never on
/// reconnect/start. The worker persists it and executes it independently of clients.
struct WorkerSetupStep: Codable, Equatable, Sendable {
    var message: String
    var arguments: [String]
    var timeout: Int64
}

struct WorkerWorkspaceSpec: Codable, Equatable, Sendable {
    var name: String
    var project: String
    var distribution: String
    var tools: [String]
    var resources: WorkerResources
    var steps: [WorkerSetupStep]

    func validate() throws {
        guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, name.count <= 120,
              project.hasPrefix("/"), project != "/", project.utf8.count <= 4096,
              !project.contains("\0"), !project.contains("\n"), !project.contains("\r"),
              !project.split(separator: "/").contains(".."),
              ["alpine", "ubuntu", "debian"].contains(distribution),
              tools.count <= 64, Set(tools).count == tools.count,
              (1...32).contains(resources.cpus), (1...64).contains(resources.memory_gib),
              (8...1024).contains(resources.disk_gib), !steps.isEmpty, steps.count <= 128,
              steps.allSatisfy({ !$0.arguments.isEmpty && (1...1800).contains($0.timeout) }),
              try JSONEncoder().encode(self).count <= 512 * 1024 else {
            throw RuntimeError("Invalid workspace configuration")
        }
    }
}

struct WorkerWorkspaceRecord: Codable, Equatable, Sendable {
    var spec: WorkerWorkspaceSpec
    var revision: Int
    var operation: String?
    var error: String?
    var recovery_backup: String?
    var reconfigure: Bool = false
    var grow_disk: Bool = false
}

struct WorkerCatalog: Codable {
    var schema: Int = 1
    var worker_id: String = UUID().uuidString.lowercased()
    var workspaces: [String: WorkerWorkspaceRecord] = [:]
}
