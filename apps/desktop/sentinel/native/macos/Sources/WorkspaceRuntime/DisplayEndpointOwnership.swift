import Darwin
import Foundation
import Synchronization

/// Identities reported by the renderer immediately after each successful bind.
/// An early exit may leave only one endpoint, or never reach display readiness.
final class DisplayEndpointOwnership: Sendable {
    private struct Identity: Sendable {
        let device: UInt64
        let inode: UInt64
        let uid: UInt32
    }
    private let paths: Set<String>
    private let identities = Mutex<[String: Identity]>([:])

    init(paths: [String]) { self.paths = Set(paths) }

    func record(_ event: [String: String]) {
        guard event["event"] == "endpoint", let path = event["path"], paths.contains(path),
              let device = event["device"].flatMap(UInt64.init),
              let inode = event["inode"].flatMap(UInt64.init),
              let uid = event["uid"].flatMap(UInt32.init), uid == getuid() else { return }
        identities.withLock { identities in
            // Each renderer binds each endpoint once. Never replace its identity.
            if identities[path] == nil { identities[path] = Identity(device: device, inode: inode, uid: uid) }
        }
    }

    /// Call after renderer exit and after its ownership reports have drained.
    func removeOwned() {
        identities.withLock { identities in
            for (path, owned) in identities {
                var current = stat()
                if lstat(path, &current) == 0, current.st_mode & S_IFMT == S_IFSOCK,
                   current.st_uid == owned.uid,
                   UInt64(truncatingIfNeeded: current.st_dev) == owned.device,
                   UInt64(current.st_ino) == owned.inode {
                    unlink(path)
                }
            }
            identities.removeAll()
        }
    }
}
