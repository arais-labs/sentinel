import Foundation
import Testing
@testable import WorkspaceRuntime

struct WorkspaceImagesTests {
    private func entry(_ distribution: WorkspaceDistribution = .ubuntu) -> WorkspaceImages.Entry {
        let digest = "sha256:" + String(repeating: "a", count: 64)
        return .init(layout: distribution.rawValue,
                     reference: "sentinel.local/workspace/\(distribution.rawValue)@\(digest)",
                     digest: digest, entrypoint: distribution.command)
    }

    @Test func rejectsInconsistentImageIdentity() throws {
        let valid = entry()
        try valid.validate(for: .ubuntu)
        var invalid = valid
        invalid.layout = "../ubuntu"
        #expect(throws: RuntimeError.self) { try invalid.validate(for: .ubuntu) }
        invalid = valid
        invalid.digest = "sha256:" + String(repeating: "b", count: 64)
        #expect(throws: RuntimeError.self) { try invalid.validate(for: .ubuntu) }
        invalid = valid
        invalid.entrypoint = ["dockerd"]
        #expect(throws: RuntimeError.self) { try invalid.validate(for: .ubuntu) }
        #expect(throws: RuntimeError.self) { try valid.validate(for: .alpine) }
    }

    @Test func existingDiskRetainsRecordedImageAndIsNeverMigratedByReading() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("sentinel-image-test-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let path = directory.appendingPathComponent("boot.json")
        #expect(throws: RuntimeError.self) {
            try WorkspaceImages.requireDiskBoot(at: path, distribution: .ubuntu)
        }
        #expect(!FileManager.default.fileExists(atPath: path.path))
        let boot = WorkspaceImages.DiskBoot(distribution: "ubuntu", image: entry())
        let data = try JSONEncoder().encode(boot)
        try data.write(to: path)
        #expect(try WorkspaceImages.requireDiskBoot(at: path, distribution: .ubuntu).image == boot.image)
        #expect(throws: RuntimeError.self) {
            try WorkspaceImages.requireDiskBoot(at: path, distribution: .debian)
        }
        #expect(try Data(contentsOf: path) == data)
    }

    @Test func rejectsInvalidDiskRecord() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("sentinel-image-test-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let path = directory.appendingPathComponent("boot.json")
        var boot = WorkspaceImages.DiskBoot(distribution: "ubuntu", image: entry())
        boot.image.layout = "../../untrusted"
        try JSONEncoder().encode(boot).write(to: path)
        #expect(throws: RuntimeError.self) {
            try WorkspaceImages.requireDiskBoot(at: path, distribution: .ubuntu)
        }
    }
}
