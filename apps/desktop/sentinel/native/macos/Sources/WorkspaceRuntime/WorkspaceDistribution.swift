import Foundation

/// Explicit image selection: never reinterpret an existing disk as another distro.
enum WorkspaceDistribution: String {
    case alpine, ubuntu, debian

    func image(alpine: String) -> String {
        switch self {
        case .alpine: return alpine
        case .ubuntu: return "docker.io/library/ubuntu@sha256:224a1869083a311ef3f13648a154ba79832fbef6364d31493642ca03082da254"
        case .debian: return "docker.io/library/debian@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132"
        }
    }

    var command: [String] {
        if self == .alpine { return ["dockerd-entrypoint.sh", "dockerd", "--host=unix:///var/run/docker.sock"] }
        // Plain Ubuntu/Debian OCI images do not contain Docker. Keep PID 1 alive
        // while the shared lifecycle installs packages, then start the daemon.
        return ["/bin/sh", "-ec", """
        while [ ! -f /run/sentinel-docker-ready ]; do sleep 1; done
        export container=docker
        if [ -d /sys/kernel/security ] && ! mountpoint -q /sys/kernel/security; then
            mount -t securityfs none /sys/kernel/security || true
        fi
        if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
            mkdir -p /sys/fs/cgroup/init
            attempts=0
            while :; do
                xargs -rn1 < /sys/fs/cgroup/cgroup.procs > /sys/fs/cgroup/init/cgroup.procs || true
                if sed -e 's/ / +/g' -e 's/^/+/' < /sys/fs/cgroup/cgroup.controllers > /sys/fs/cgroup/cgroup.subtree_control; then break; fi
                attempts=$((attempts + 1))
                [ "$attempts" -lt 10 ] || exit 1
                sleep 1
            done
        fi
        mount --make-rshared /
        exec dockerd --host=unix:///var/run/docker.sock
        """]
    }
}
