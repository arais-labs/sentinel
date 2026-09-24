import Containerization
import ContainerizationError
import Foundation

/// Native OS images are built and validated before shipping the runtime. Desktop
/// provisioning selects packages, but cannot change this per-disk boot contract.
struct WorkspaceImages {
    struct Entry: Codable, Equatable {
        var layout: String
        var reference: String
        var digest: String
        var entrypoint: [String]

        func validate(for distribution: WorkspaceDistribution) throws {
            guard layout == distribution.rawValue, entrypoint == distribution.command,
                  reference.hasSuffix("@" + digest) else {
                throw RuntimeError("Invalid native Linux image entry")
            }
            try WorkspaceDistribution.validateImageReference(reference)
        }
    }
    struct Manifest: Decodable {
        var schema: Int
        var boot_contract: Int
        var platform: String
        var images: [String: Entry]
    }
    struct DiskBoot: Codable {
        var version = 1
        var distribution: String
        var image: Entry
    }
    let directory: URL
    let manifest: Manifest

    init(directory: URL) throws {
        self.directory = directory
        let file = directory.appendingPathComponent("manifest.json")
        guard FileManager.default.fileExists(atPath: file.path) else {
            throw RuntimeError("This runtime package is missing its native Linux workspace images")
        }
        manifest = try JSONDecoder().decode(Manifest.self, from: Data(contentsOf: file))
        guard manifest.schema == 1, manifest.boot_contract == 1, manifest.platform == "linux/arm64" else {
            throw RuntimeError("Unsupported native Linux image manifest")
        }
        for (name, entry) in manifest.images {
            guard let distribution = WorkspaceDistribution(rawValue: name) else {
                throw RuntimeError("Invalid native Linux image entry")
            }
            try entry.validate(for: distribution)
        }
    }

    func entry(for distribution: WorkspaceDistribution) throws -> Entry {
        guard let entry = manifest.images[distribution.rawValue] else {
            throw RuntimeError("This runtime package has no \(distribution.rawValue) workspace image")
        }
        return entry
    }

    func prepare(_ entry: Entry, store: ImageStore) async throws {
        do {
            let image = try await store.get(reference: entry.reference)
            guard image.digest == entry.digest else { throw RuntimeError("Workspace image digest mismatch") }
        } catch let error as ContainerizationError where error.code == .notFound {
            let images = try await store.load(from: directory.appendingPathComponent(entry.layout))
            guard images.count == 1, images[0].reference == entry.reference, images[0].digest == entry.digest else {
                throw RuntimeError("Imported workspace image does not match its manifest")
            }
        }
        let config = try await store.get(reference: entry.reference).config(for: .init(arch: "arm64", os: "linux"))
        guard config.config?.entrypoint == entry.entrypoint else {
            throw RuntimeError("Workspace image does not boot its native init")
        }
    }

    static func requireDiskBoot(at path: URL, distribution: WorkspaceDistribution) throws -> DiskBoot {
        guard FileManager.default.fileExists(atPath: path.path) else {
            throw RuntimeError("Reinstall this workspace to use native Linux services. Its existing disk has not been changed.")
        }
        let boot = try JSONDecoder().decode(DiskBoot.self, from: Data(contentsOf: path))
        guard boot.version == 1, boot.distribution == distribution.rawValue,
              boot.image.entrypoint == distribution.command else {
            throw RuntimeError("Workspace boot configuration does not match its Linux distribution")
        }
        try boot.image.validate(for: distribution)
        return boot
    }
}
