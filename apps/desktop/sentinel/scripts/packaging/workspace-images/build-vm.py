"""Build OCI roots in an owned persistent Sentinel DinD VM, never host Docker.

Requires an already built helper whose private builder image supports Docker.
The VM shares only a staged copy of image recipes and this builder's outputs.
Its private OS disk, installed tools and Docker/BuildKit caches survive runs.
"""

import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid

DESKTOP = Path(__file__).resolve().parents[3]
CACHE = DESKTOP / "build/workspace-image-builders"


def builder_identity(config, kernel):
    digest = hashlib.sha256()
    with kernel.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    identity = json.dumps(
        {
            "schema": 1,
            "image": config["buildImage"],
            "init": config["initImage"],
            "kernel": digest.hexdigest(),
        },
        sort_keys=True,
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "sentinel-workspace-images:" + identity))


def build_config(runtime):
    # Private compiler images belong to the source lock, not the shipped
    # workspace catalog. Use the init image paired with the packaged helper.
    source = json.loads((DESKTOP / "runtime.lock.json").read_bytes())["platforms"]["macos-arm64"][
        "workspaceRuntime"
    ]
    packaged = json.loads((runtime / "manifest.json").read_bytes())
    return {"buildImage": source["buildImage"], "initImage": packaged["initImage"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--runtime", type=Path, default=DESKTOP / "build/macos-arm64/runtime/workspace-runtime"
    )
    parser.add_argument("--distribution", action="append", choices=["ubuntu", "debian", "alpine"])
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    shared = root / "shared"
    shutil.copytree(DESKTOP / "native/workspace-images", shared / "native/workspace-images")
    scripts = shared / "scripts/packaging/workspace-images"
    scripts.mkdir(parents=True)
    shutil.copy2(Path(__file__).with_name("build.py"), scripts / "build.py")
    helper = root / "sentinel-workspace-runtime"
    shutil.copy2(args.runtime / helper.name, helper)
    kernel = root / "kernel"
    shutil.copy2(args.runtime / "kernel", kernel)
    shutil.copy2(args.runtime / "manifest.json", root / "manifest.json")
    config = build_config(args.runtime)
    bases = json.loads((shared / "native/workspace-images/bases.json").read_bytes())
    workspace = builder_identity(config, kernel)
    # BuildKit revisions get their own builder without discarding the VM's
    # installed packages, image store or older builders' layer caches.
    builder = (
        "sentinel-native-images-"
        + hashlib.sha256(bases["buildkit_image"].encode()).hexdigest()[:16]
    )
    cpus = min(32, os.cpu_count() or 4)
    CACHE.mkdir(parents=True, exist_ok=True)
    replies = queue.Queue()
    with (
        (CACHE / "build.lock").open("a") as lock,
        (root / "runtime.log").open("w") as runtime_log,
        (root / "build.log").open("wb") as build_log,
    ):
        # The runtime image store and disks are shared by all invocations.
        # Hold ownership through VM shutdown, including failed builds.
        print(f"Waiting for workspace image builder cache: {CACHE}", flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(f"Using persistent image builder {workspace} ({cpus} cores)", flush=True)
        process = subprocess.Popen(
            [str(helper), str(CACHE / "runtime"), str(kernel), config["initImage"]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=runtime_log,
            text=True,
        )

        def read():
            try:
                for line in process.stdout:
                    response = json.loads(line)
                    if response.get("event") == "output":
                        data = base64.b64decode(response["data"])
                        build_log.write(data)
                        build_log.flush()
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                    else:
                        replies.put(response)
            finally:
                replies.put({"event": "fatal", "error": "Owned build helper exited"})

        reader = threading.Thread(target=read, daemon=True)
        reader.start()

        def receive(identifier=None, event=None, timeout=1800):
            deadline = time.monotonic() + timeout
            while True:
                response = replies.get(timeout=max(0, deadline - time.monotonic()))
                if response.get("event") == "fatal":
                    raise RuntimeError(response["error"])
                if (identifier and response.get("id") == identifier) or (
                    event and response.get("event") == event
                ):
                    if response.get("error"):
                        raise RuntimeError(response["error"])
                    return response

        def request(action, **values):
            identifier = str(uuid.uuid4())
            print(f"Build VM request: {action}", flush=True)
            process.stdin.write(
                json.dumps({"id": identifier, "action": action, "workspace": workspace, **values})
                + "\n"
            )
            process.stdin.flush()
            response = receive(identifier=identifier)
            print(f"Build VM response: {action} ({response.get('event', 'reply')})", flush=True)
            return response

        def command(arguments):
            child = str(uuid.uuid4())
            print(f"Build VM command: {arguments[0]}", flush=True)
            request("process_start", process=child, arguments=arguments, terminal=False)
            response = receive(event="exit")
            if response.get("process") != child or response.get("exitCode") != 0:
                raise RuntimeError(f"Image build command failed: {response}")

        ready = False
        try:
            receive(event="ready")
            ready = True
            request(
                "start",
                project=str(shared),
                cpus=cpus,
                memory_gib=6,
                disk_gib=64,
                image_reference=config["buildImage"],
            )
            command(
                [
                    "sh",
                    "-ec",
                    "if ! apk info -e python3 docker-cli-buildx >/dev/null; then "
                    "apk add --no-cache python3 docker-cli-buildx; fi; docker info; "
                    'if ! docker buildx inspect "$2" >/dev/null 2>&1; then '
                    'docker buildx create --name "$2" --driver docker-container '
                    '--driver-opt image="$1"; fi; docker buildx inspect "$2" --bootstrap',
                    "sentinel",
                    bases["buildkit_image"],
                    builder,
                ]
            )
            for distribution in args.distribution or ["ubuntu", "debian", "alpine"]:
                destination = shared / "artifacts" / distribution
                command(
                    [
                        "python3",
                        str(scripts / "build.py"),
                        str(destination),
                        "--distribution",
                        distribution,
                        "--builder",
                        builder,
                    ]
                )
                print(f"QUALIFIED OCI ARTIFACT: {destination / 'manifest.json'}", flush=True)
            print(f"Completed image artifacts: {shared / 'artifacts'}", flush=True)
        finally:
            try:
                if ready and process.poll() is None:
                    request("stop")
            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                reader.join(timeout=5)
            print(
                f"Owned build VM stopped; persistent cache retained at {CACHE}; artifacts and logs at {root}",
                flush=True,
            )


if __name__ == "__main__":
    main()
