"""Build guest graphics in Linux containers or a local macOS VM; verify CI inputs."""

import fcntl
import hashlib
import json
import os
import platform
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import uuid
from pathlib import Path

source = Path(__file__).resolve().parent
desktop = source.parents[2]
dest = Path(sys.argv[1]).resolve()
cache = desktop / "build/graphics-sources/guest"
cache.mkdir(parents=True, exist_ok=True)
config = json.loads((desktop / "runtime.lock.json").read_text())["platforms"]["macos-arm64"][
    "workspaceRuntime"
]
libc = sys.argv[2] if len(sys.argv) > 2 else "musl"
if libc not in {"musl", "glibc"}:
    raise ValueError("Unknown graphics libc")
script = (source / "build-guest.sh").read_text()
if libc == "glibc":
    script = "export SENTINEL_GRAPHICS_LIBC=glibc\n" + script
key = hashlib.sha256(
    script.encode()
    + json.dumps(config, sort_keys=True).encode()
    + (
        (desktop / "native/macos/Sources/WorkspaceRuntime/WorkspaceDistribution.swift").read_bytes()
        if libc == "glibc"
        else b""
    )
).hexdigest()
artifact = cache / (key + ".tar.xz")
manifest = cache / (key + ".json")


def sha(file):
    result = hashlib.sha256()
    with file.open("rb") as data:
        for chunk in iter(lambda: data.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def record_artifact(archive):
    with tarfile.open(archive) as bundle:
        version = bundle.extractfile("./version").read().decode().strip()
    archive.replace(artifact)
    manifest.write_text(
        json.dumps({"version": version, "sha256": sha(artifact), "buildKey": key}) + "\n"
    )


def build_linux():
    if platform.machine() not in {"aarch64", "arm64"}:
        raise RuntimeError("Guest graphics builds require a native Linux ARM64 host")
    image = config["workspaceImage"]
    if libc == "glibc":
        distribution = desktop / "native/macos/Sources/WorkspaceRuntime/WorkspaceDistribution.swift"
        image = re.search(r'case \.ubuntu: return "([^"]+)"', distribution.read_text()).group(1)
    with tempfile.TemporaryDirectory(prefix="output-", dir=cache) as output:
        run(
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/arm64",
            "--interactive",
            "--volume",
            f"{output}:/output",
            "--entrypoint",
            "sh",
            image,
            "-s",
            input=(
                script + "\ntar -C /opt/sentinel/graphics -cJf /output/graphics.tar.xz .\n"
            ).encode(),
        )
        record_artifact(Path(output) / "graphics.tar.xz")


def build():
    kernel = cache / config["kernelFileSha256"]
    if not kernel.exists() or sha(kernel) != config["kernelFileSha256"]:
        archive = cache / "kernel.download"
        try:
            run(
                "curl",
                "-fL",
                "--retry",
                "2",
                "--connect-timeout",
                "15",
                "--max-time",
                "1200",
                config["kernelUrl"],
                "-o",
                archive,
            )
            if sha(archive) != config["kernelSha256"]:
                raise RuntimeError("Build kernel archive checksum mismatch")
            with kernel.open("wb") as target:
                run(
                    "/usr/bin/tar",
                    "-xOf",
                    archive,
                    config["kernelArchivePath"],
                    stdout=target,
                )
            if sha(kernel) != config["kernelFileSha256"]:
                raise RuntimeError("Build kernel checksum mismatch")
        finally:
            archive.unlink(missing_ok=True)

    workspace = str(uuid.uuid4())
    with (
        tempfile.TemporaryDirectory(prefix="output-", dir=cache) as output,
        (cache / "build.log").open("w") as log,
    ):
        helper = subprocess.Popen(
            [
                str(dest.parent / "sentinel-workspace-runtime"),
                str(cache / "runtime"),
                str(kernel),
                config["initImage"],
                config["workspaceImage"],
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
        )
        replies = queue.Queue()

        def read():
            try:
                for line in helper.stdout:
                    replies.put(json.loads(line))
            finally:
                replies.put({"event": "fatal", "error": "Graphics build helper exited"})

        threading.Thread(target=read, daemon=True).start()

        def receive(identifier=None, timeout=1800):
            while True:
                reply = replies.get(timeout=timeout)
                if reply.get("event") == "fatal":
                    raise RuntimeError(reply.get("error"))
                if (identifier and reply.get("id") == identifier) or (
                    not identifier and reply.get("event") == "ready"
                ):
                    if reply.get("error"):
                        raise RuntimeError(reply["error"])
                    return reply

        def request(action, **values):
            identifier = str(uuid.uuid4())
            helper.stdin.write(
                json.dumps(
                    {
                        "id": identifier,
                        "action": action,
                        "workspace": workspace,
                        **values,
                    }
                )
                + "\n"
            )
            helper.stdin.flush()
            return receive(identifier)

        ready = False
        try:
            print("Precompiling desktop graphics in the build VM…", flush=True)
            receive()
            ready = True
            request(
                "start",
                project=output,
                cpus=2,
                memory_gib=4,
                disk_gib=16,
                **({"distribution": "ubuntu"} if libc == "glibc" else {}),
            )
            archive = Path(output) / "graphics.tar.xz"
            result = request(
                "exec",
                arguments=[
                    "sh",
                    "-c",
                    script
                    + "\ntar -C /opt/sentinel/graphics -cJf "
                    + shlex.quote(str(archive))
                    + " .\n",
                ],
                timeout=1200,
            )
            log.write(result.get("stdout", "") + result.get("stderr", ""))
            log.flush()
            if result.get("exitCode") != 0:
                raise RuntimeError(f'Desktop graphics build failed; see {cache / "build.log"}')
            record_artifact(archive)
        finally:
            try:
                if ready and helper.poll() is None:
                    request("delete")
            finally:
                helper.stdin.close()
                try:
                    helper.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    helper.kill()
                    helper.wait()


bundle = "mesa-linux-arm64" + ("-glibc" if libc == "glibc" else "")
prebuilt = os.environ.get("SENTINEL_GUEST_GRAPHICS_DIR")
if prebuilt:
    supplied = Path(prebuilt)
    supplied_archive = supplied / (bundle + ".tar.xz")
    supplied_manifest = supplied / (bundle + ".json")
    metadata = json.loads(supplied_manifest.read_text())
    if metadata.get("buildKey") != key or metadata.get("sha256") != sha(supplied_archive):
        raise RuntimeError(f"Guest graphics artifact does not match build inputs: {bundle}")
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(supplied_archive, dest / supplied_archive.name)
    shutil.copy2(supplied_manifest, dest / supplied_manifest.name)
    print(f"Verified prebuilt guest graphics: {bundle}", flush=True)
    sys.exit(0)
if os.environ.get("CI") and sys.platform == "darwin":
    raise RuntimeError("macOS CI requires SENTINEL_GUEST_GRAPHICS_DIR; guest VMs are not supported")

with (cache / "build.lock").open("w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        valid = json.loads(manifest.read_text())["sha256"] == sha(artifact)
    except (OSError, ValueError, KeyError):
        valid = False
    if not valid:
        if sys.platform == "linux":
            build_linux()
        else:
            build()
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifact, dest / (bundle + ".tar.xz"))
    shutil.copy2(manifest, dest / (bundle + ".json"))
    print("Precompiled desktop graphics bundle ready.", flush=True)
