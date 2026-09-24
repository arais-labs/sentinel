import Containerization
import CryptoKit
import Foundation

/// Graphics archives are already part of the worker release. They never need
/// to make a round trip through a viewing laptop during workspace setup.
extension WorkspaceRuntime {
    @MainActor static func installGuestBrowserGraphics(_ id: String, container: LinuxContainer, resources: URL) async throws {
        struct Manifest: Decodable { var sha256: String; var version: String; var size: Int }
        let manifest = try JSONDecoder().decode(Manifest.self, from: Data(contentsOf: resources.appendingPathComponent("gpu-2404.json")))
        let script = try String(contentsOf: resources.appendingPathComponent("install-browser-graphics.py"), encoding: .utf8)
        let current = try await executeGuest(Request(id: UUID().uuidString, action: "exec", workspace: id,
            arguments: ["python3", "-c", script, "check", manifest.sha256, manifest.version], timeout: 30), container: container)
        if current.exitCode == 0 { return }
        let archive = try Data(contentsOf: resources.appendingPathComponent("gpu-2404.snap"), options: .mappedIfSafe)
        guard archive.count == manifest.size,
              SHA256.hash(data: archive).map({ String(format: "%02x", $0) }).joined() == manifest.sha256 else {
            throw RuntimeError("Bundled browser graphics checksum mismatch")
        }
        let input = Input()
        for offset in stride(from: 0, to: archive.count, by: 32768) {
            input.pair.continuation.yield(archive.subdata(in: offset..<min(offset + 32768, archive.count)))
        }
        input.pair.continuation.finish()
        let result = try await executeGuest(Request(id: UUID().uuidString, action: "exec", workspace: id,
            arguments: ["python3", "-u", "-c", script, "install", manifest.sha256, manifest.version, String(archive.count)], timeout: 600),
            container: container, input: input)
        guard result.exitCode == 0 else { throw RuntimeError(result.stderr ?? "Browser graphics installation failed") }
    }

    @MainActor static func installGuestGraphics(_ id: String, container: LinuxContainer, resources: URL, distribution: String) async throws {
        struct Manifest: Decodable { var sha256: String; var version: String }
        let bundle = distribution == "alpine" ? "mesa-linux-arm64" : "mesa-linux-arm64-glibc"
        let script = try String(contentsOf: resources.appendingPathComponent("install-guest.py"), encoding: .utf8)
        for (bundle, kind) in [("kernel-linux-arm64", "kernel"), (bundle, "graphics"), ("desktop-runtime", "desktop")] {
            let manifest = try JSONDecoder().decode(Manifest.self, from: Data(contentsOf: resources.appendingPathComponent(bundle + ".json")))
            let current = try await executeGuest(Request(id: UUID().uuidString, action: "exec", workspace: id,
                arguments: ["cat", "/opt/sentinel/\(kind)/bundle.sha256"], timeout: 5), container: container)
            if current.exitCode == 0 && current.stdout?.trimmingCharacters(in: .whitespacesAndNewlines) == manifest.sha256 { continue }
            let archive = try Data(contentsOf: resources.appendingPathComponent(bundle + ".tar.xz"), options: .mappedIfSafe)
            guard SHA256.hash(data: archive).map({ String(format: "%02x", $0) }).joined() == manifest.sha256 else {
                throw RuntimeError("Bundled desktop graphics checksum mismatch")
            }
            let input = Input()
            for offset in stride(from: 0, to: archive.count, by: 32768) {
                input.pair.continuation.yield(archive.subdata(in: offset..<min(offset + 32768, archive.count)))
            }
            input.pair.continuation.finish()
            let result = try await executeGuest(Request(id: UUID().uuidString, action: "exec", workspace: id,
                arguments: ["python3", "-u", "-c", script, String(archive.count), manifest.sha256, manifest.version, kind], timeout: kind == "graphics" ? 600 : 120), container: container, input: input)
            guard result.exitCode == 0 else { throw RuntimeError(result.stderr ?? "Desktop graphics installation failed") }
        }
    }
}
