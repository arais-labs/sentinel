import Foundation
import Darwin

/// Small update control plane: no VM/image initialization, including when the
/// installed service cannot start. OS locks are released on SSH EOF or a crash.
enum RuntimeUpdate {
    static let inheritedOwner: Int32 = 198

    static func lock(_ path: String, _ mode: Int32) throws -> Int32 {
        let fd = open(path, O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw RuntimeError("Cannot open runtime ownership lock") }
        guard flock(fd, mode | LOCK_NB) == 0 else {
            let code = errno
            close(fd)
            if code == EWOULDBLOCK { throw RuntimeError("Runtime is busy; another service or update owns the lock") }
            throw RuntimeError("Cannot establish runtime ownership")
        }
        return fd
    }

    static func ownerFree(_ root: String) throws -> Bool {
        let fd = open(root + "/owner.lock", O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw RuntimeError("Cannot inspect runtime ownership") }
        defer { close(fd) }
        if flock(fd, LOCK_EX | LOCK_NB) == 0 { return true }
        guard errno == EWOULDBLOCK else { throw RuntimeError("Cannot inspect runtime ownership") }
        return false
    }

    static func write(_ value: Any, to path: String) throws {
        let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        try data.write(to: URL(fileURLWithPath: path), options: [.atomic])
        let fd = open(path, O_RDONLY | O_NOFOLLOW)
        guard fd >= 0 else { throw RuntimeError("Cannot flush runtime update state") }
        defer { close(fd) }
        guard fsync(fd) == 0 else { throw RuntimeError("Cannot flush runtime update state") }
        let parent = open(URL(fileURLWithPath: path).deletingLastPathComponent().path, O_RDONLY)
        if parent >= 0 { defer { close(parent) }; _ = fsync(parent) }
    }

    static func read(_ path: String) throws -> Any {
        guard FileManager.default.fileExists(atPath: path) else { return NSNull() }
        return try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path)))
    }

    static func reply(_ value: [String: Any]) throws {
        var bytes = try JSONSerialization.data(withJSONObject: value)
        bytes.append(10)
        try FileHandle.standardOutput.write(contentsOf: bytes)
    }

    static func serve(_ root: String, leaseSeconds: Double = 60) throws {
        signal(SIGPIPE, SIG_IGN)
        signal(SIGHUP, SIG_IGN)
        let lease = try lock(root + "/update.lock", LOCK_EX)
        defer { close(lease) }
        // SSH can remain half-open after a network partition. A bounded lease
        // prevents an abandoned updater from holding this machine indefinitely.
        let expiry = DispatchSource.makeTimerSource(queue: .global())
        expiry.setEventHandler { exit(1) }
        expiry.schedule(deadline: .now() + leaseSeconds)
        expiry.resume()
        defer { expiry.cancel() }
        try reply(["event": "locked"])
        while let line = readLine() {
            do {
                guard let request = try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any],
                      let action = request["action"] as? String else { throw RuntimeError("Invalid update request") }
                expiry.schedule(deadline: .now() + leaseSeconds)
                switch action {
                case "heartbeat": try reply(["ok": true])
                case "inspect":
                    try reply(["owner_free": try ownerFree(root),
                               "migrations": try RuntimeMigrations.completed(root: URL(fileURLWithPath: root)),
                               "manifest": try read(root + "/manifest.json"),
                               "journal": try read(root + "/update.json")])
                case "journal":
                    guard let journal = request["journal"] as? [String: Any] else { throw RuntimeError("Missing update state") }
                    try write(journal, to: root + "/update.json")
                    try reply(["ok": true])
                case "migration_status":
                    try reply(RuntimeMigrations.status(root: URL(fileURLWithPath: root)))
                case "migration_plan", "migrate":
                    let inputs = request["inputs"] as? [String: Any] ?? [:]
                    let directory = URL(fileURLWithPath: root)
                    if action == "migrate" {
                        let owner = try lock(root + "/owner.lock", LOCK_EX)
                        defer { close(owner) }
                        try RuntimeMigrations.run(root: directory, inputs: inputs)
                    } else { try RuntimeMigrations.plan(root: directory, inputs: inputs) }
                    try reply(["ok": true])
                case "activate":
                    guard let manifest = request["manifest"] as? [String: Any],
                          manifest["updateProtocol"] as? Int == 1,
                          let exe = manifest["executable"] as? String,
                          let kernel = manifest["kernel"] as? String,
                          let initial = manifest["initImage"] as? String,
                          let workspace = manifest["workspaceImage"] as? String,
                          exe.hasPrefix(root + "/releases/"), kernel.hasPrefix(root + "/releases/")
                    else { throw RuntimeError("Unsupported runtime update manifest") }
                    // Keep ownership continuously from manifest activation into
                    // the new process. Even a legacy client cannot race a start.
                    let owner = try lock(root + "/owner.lock", LOCK_EX)
                    defer { close(owner) }
                    try RuntimeMigrations.checkActivation(root: URL(fileURLWithPath: root), manifest: manifest)
                    try write(manifest, to: root + "/manifest.json")
                    let pid = try launch([exe, "--owned-service", root, kernel, initial, workspace], owner: owner, root: root)
                    try reply(["ok": true, "pid": pid])
                default: throw RuntimeError("Unknown runtime update action")
                }
            } catch {
                try reply(["error": String(describing: error)])
            }
        }
    }

    static func launch(_ args: [String], owner: Int32, root: String) throws -> pid_t {
        var actions: posix_spawn_file_actions_t?
        guard posix_spawn_file_actions_init(&actions) == 0 else { throw RuntimeError("Cannot prepare runtime launch") }
        defer { posix_spawn_file_actions_destroy(&actions) }
        guard posix_spawn_file_actions_adddup2(&actions, owner, inheritedOwner) == 0,
              posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0) == 0,
              posix_spawn_file_actions_addopen(&actions, STDOUT_FILENO, root + "/service.log", O_WRONLY | O_CREAT | O_APPEND, 0o600) == 0,
              posix_spawn_file_actions_adddup2(&actions, STDOUT_FILENO, STDERR_FILENO) == 0
        else { throw RuntimeError("Cannot configure runtime launch") }
        let argv = args.map { strdup($0) } + [nil]
        let environment: [String] = ["PATH=/usr/bin:/bin:/usr/sbin:/sbin", "LANG=en_US.UTF-8"]
        let env = environment.map { strdup($0) } + [nil]
        defer { argv.forEach { free($0) }; env.forEach { free($0) } }
        var pid: pid_t = 0
        let result = argv.withUnsafeBufferPointer { a in
            env.withUnsafeBufferPointer { e in
                posix_spawn(&pid, args[0], &actions, nil, a.baseAddress!, e.baseAddress!)
            }
        }
        guard result == 0 else { throw RuntimeError("Cannot launch replacement runtime: \(String(cString: strerror(result)))") }
        return pid
    }
}
