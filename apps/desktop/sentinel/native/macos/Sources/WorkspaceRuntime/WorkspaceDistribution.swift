import Containerization
import Foundation

/// Explicit image selection: never reinterpret an existing disk as another distro.
enum WorkspaceDistribution: String {
    case alpine, ubuntu, debian

    static func validateImageReference(_ reference: String) throws {
        guard reference.range(of: #"^[^\s@]+@sha256:[a-f0-9]{64}$"#, options: .regularExpression) != nil else {
            throw RuntimeError("Workspace images must use an immutable SHA-256 reference")
        }
    }

    var command: [String] { self == .alpine ? ["/sbin/openrc-init"] : ["/sbin/init"] }

    // Containerization.stop() force-kills processes; ask native init to stop
    // its services first. These match the shipped OCI StopSignal contracts.
    var shutdownSignal: Signal { self == .alpine ? .term : Signal.Linux.rtmin(offset: 3) }

    // Build VMs are explicit raw-start tooling, never a workspace boot fallback.
    // Compiler images need only a live process; the Alpine build image is DinD.
    var buildCommand: [String] {
        if self == .alpine { return ["dockerd-entrypoint.sh", "dockerd", "--host=unix:///var/run/docker.sock"] }
        return ["/bin/sleep", "infinity"]
    }
}
