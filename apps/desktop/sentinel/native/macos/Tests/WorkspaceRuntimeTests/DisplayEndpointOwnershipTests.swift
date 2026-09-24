import Darwin
import Foundation
import Testing
@testable import WorkspaceRuntime

struct DisplayEndpointOwnershipTests {
    @Test func readinessStreamsBeforePipeWriterCloses() async throws {
        let pipe = Pipe(), output = DisplayReadiness()
        defer { try? pipe.fileHandleForWriting.close() }
        let reader = Task { await output.read(from: pipe.fileHandleForReading) }
        try pipe.fileHandleForWriting.write(contentsOf: Data("{\"event\":\"ready\"}\n".utf8))
        // The writer stays open until readiness arrives. A buffered read that
        // waits for 4096 bytes or EOF must time out instead of passing this test.
        try await runtimeDeadline(2) { try await output.wait() }
        try pipe.fileHandleForWriting.close()
        try await runtimeDeadline(2) { await reader.value }
    }

    private func bind(_ path: String) throws -> FileHandle {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        try #require(fd >= 0)
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        var address = try RemoteControl.address(path)
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        try #require(result == 0)
        return handle
    }

    private func event(_ path: String) throws -> [String: String] {
        var identity = stat()
        try #require(lstat(path, &identity) == 0)
        return ["event": "endpoint", "path": path,
                "device": String(UInt64(truncatingIfNeeded: identity.st_dev)),
                "inode": String(identity.st_ino), "uid": String(identity.st_uid)]
    }

    @Test func removesReportedSocketsAfterAbruptExitWithoutReadiness() async throws {
        let root = "/tmp/sentinel-endpoint-" + UUID().uuidString
        try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(atPath: root) }
        let paths = [root + "/video", root + "/control"]
        let ownership = DisplayEndpointOwnership(paths: paths)
        let output = DisplayReadiness(endpoints: ownership)
        for path in paths {
            let socket = try bind(path)
            var report = try JSONSerialization.data(withJSONObject: event(path))
            report.append(10)
            // Pipe reads can split ownership reports at any byte boundary.
            try output.write(report.prefix(7))
            try output.write(report.dropFirst(7))
            try socket.close() // A crashed child closes descriptors, leaving paths.
        }
        output.close()
        await #expect(throws: RuntimeError.self) { try await output.wait() }
        ownership.removeOwned()
        ownership.removeOwned()
        for path in paths {
            var identity = stat()
            #expect(lstat(path, &identity) == -1)
            #expect(errno == ENOENT)
        }
    }

    @Test func partialStartupCannotClaimForeignOrReplacedEndpoints() throws {
        for kind in ["socket", "file", "symlink"] {
            let root = "/tmp/sentinel-endpoint-" + UUID().uuidString
            try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: false)
            defer { try? FileManager.default.removeItem(atPath: root) }
            let video = root + "/video", control = root + "/control", foreign = root + "/foreign"
            let ownership = DisplayEndpointOwnership(paths: [video, control])
            let original = try bind(video)
            defer { try? original.close() }
            ownership.record(try event(video))
            let foreignSocket = try bind(foreign)
            defer { try? foreignSocket.close() }
            ownership.record(try event(foreign)) // Not one of this display's paths.
            let controlSocket = try bind(control) // Bind failed: no report from our renderer.
            defer { try? controlSocket.close() }
            try #require(unlink(video) == 0)
            var replacement: FileHandle?
            defer { try? replacement?.close() }
            switch kind {
            case "socket": replacement = try bind(video)
            case "file": try Data("preserve".utf8).write(to: URL(fileURLWithPath: video))
            default: try FileManager.default.createSymbolicLink(atPath: video, withDestinationPath: "missing")
            }
            let before = try [video, control, foreign].map { try event($0) }
            ownership.removeOwned()
            #expect(try [video, control, foreign].map { try event($0) } == before)
        }
    }
}
