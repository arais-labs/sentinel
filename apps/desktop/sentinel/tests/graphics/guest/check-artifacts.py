"""Boot packaged kernels in disposable workspaces and check mapped-memory IO."""

import hashlib
import json
import queue
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import threading
import uuid
from pathlib import Path


def main():
    desktop = Path(__file__).resolve().parents[3]
    runtime = desktop / "build/macos-arm64/runtime/workspace-runtime"
    artifacts = runtime / "graphics"
    cache = desktop / "build/graphics-sources/guest/probe-runtime"
    config = json.loads((desktop / "runtime.lock.json").read_text())["platforms"]["macos-arm64"][
        "workspaceRuntime"
    ]
    for name in ("kernel-linux-arm64", "mesa-linux-arm64", "mesa-linux-arm64-glibc"):
        archive = artifacts / f"{name}.tar.xz"
        metadata = json.loads((artifacts / f"{name}.json").read_text())
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != metadata["sha256"]:
            raise RuntimeError(f"Artifact checksum mismatch: {name}")

    with tempfile.TemporaryDirectory(prefix="probe-", dir=cache.parent) as temporary:
        project = Path(temporary)
        with tarfile.open(artifacts / "kernel-linux-arm64.tar.xz") as bundle:
            release = bundle.extractfile("./kernelrelease").read().decode().strip()
            with (project / "Image").open("wb") as output:
                shutil.copyfileobj(bundle.extractfile("./Image"), output)
            bundle.extractall(
                project / "kernel",
                members=[member for member in bundle if member.name.startswith("./lib/modules/")],
                filter="data",
            )
        for suffix in ("", "-glibc"):
            with tarfile.open(artifacts / f"mesa-linux-arm64{suffix}.tar.xz") as bundle:
                executable = project / f"mapped-memory-check{suffix}"
                with executable.open("wb") as output:
                    shutil.copyfileobj(
                        bundle.extractfile("./bin/sentinel-mapped-memory-check"), output
                    )
                executable.chmod(0o755)
        replies = queue.Queue()
        with (project / "helper.log").open("w+") as log:
            helper = subprocess.Popen(
                [
                    str(runtime / "sentinel-workspace-runtime"),
                    str(cache),
                    str(project / "Image"),
                    config["initImage"],
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
            )

            def read():
                try:
                    for line in helper.stdout:
                        replies.put(json.loads(line))
                finally:
                    replies.put({"event": "fatal", "error": "Probe helper exited"})

            threading.Thread(target=read, daemon=True).start()

            def receive(identifier=None):
                while True:
                    reply = replies.get(timeout=600)
                    if reply.get("error"):
                        raise RuntimeError(reply["error"])
                    if (identifier and reply.get("id") == identifier) or (
                        identifier is None and reply.get("event") == "ready"
                    ):
                        return reply

            def request(workspace, action, **values):
                identifier = str(uuid.uuid4())
                helper.stdin.write(
                    json.dumps(
                        {
                            "id": identifier,
                            "workspace": workspace,
                            "action": action,
                            **values,
                        }
                    )
                    + "\n"
                )
                helper.stdin.flush()
                return receive(identifier)

            try:
                receive()
                for suffix, distribution in (("", "alpine"), ("-glibc", "ubuntu")):
                    workspace = str(uuid.uuid4())
                    try:
                        request(
                            workspace,
                            "start",
                            project=str(project),
                            cpus=2,
                            memory_gib=2,
                            disk_gib=8,
                            distribution=distribution,
                        )
                        command = (
                            "set -eu\n"
                            f'test "$(uname -r)" = {shlex.quote(release)}\n'
                            + (
                                "apk add --no-cache kmod >/dev/null\n"
                                if distribution == "alpine"
                                else "apt-get update -qq && apt-get install -y --no-install-recommends kmod >/dev/null\n"
                            )
                            + "mkdir -p /lib/modules\n"
                            f"cp -a {shlex.quote(str(project / 'kernel/lib/modules' / release))} /lib/modules/\n"
                            "/sbin/depmod -a\n"
                            "/sbin/modprobe virtio_gpu\n"
                            "/sbin/modprobe virtio-lo\n"
                            "/sbin/modprobe uinput\n"
                            "test -d /sys/module/virtio_gpu\n"
                            "test -d /sys/module/virtio_lo\n"
                            "test -c /dev/virtio-lo\n"
                            "test -c /dev/uinput\n"
                            "printf '%s\\n' 'PASS packaged virtio-gpu/virtio-lo/uinput dependencies and devices'\n"
                            f"timeout 20 {shlex.quote(str(project / f'mapped-memory-check{suffix}'))}\n"
                            "printf 'PASS %s: packaged kernel/devices/mapped-memory capture/accept\\n' \"$(uname -r)\"\n"
                        )
                        result = request(
                            workspace,
                            "exec",
                            arguments=["sh", "-c", command],
                            timeout=120,
                        )
                        print(distribution, json.dumps(result), flush=True)
                        if result.get("exitCode") != 0:
                            raise RuntimeError(
                                f"Packaged mapped-memory probe failed: {distribution}"
                            )
                    finally:
                        if helper.poll() is None:
                            request(workspace, "delete")
            finally:
                helper.stdin.close()
                try:
                    helper.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    helper.kill()
                    helper.wait()


if __name__ == "__main__":
    main()
