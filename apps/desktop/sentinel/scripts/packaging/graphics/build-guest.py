"""Build guest graphics in Linux containers or a local macOS VM; verify CI inputs."""

import fcntl
import hashlib
import json
import os
import platform
import queue
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path

source = Path(__file__).resolve().parent
desktop = source.parents[2]
dest = Path(sys.argv[1]).resolve()
runtime_helper = (
    Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else dest.parent / "sentinel-workspace-runtime"
)
cache = desktop / "build/graphics-sources/guest"
cache.mkdir(parents=True, exist_ok=True)
config = json.loads((desktop / "runtime.lock.json").read_text())["platforms"]["macos-arm64"][
    "workspaceRuntime"
]
build_target = sys.argv[2] if len(sys.argv) > 2 else "musl"
klipper = None
if build_target in {"klipper-debian", "klipper-ubuntu", "klipper-alpine"}:
    from klipper import AlpineKlipperTarget, KlipperTarget

    distribution = build_target.removeprefix("klipper-")
    klipper = (
        AlpineKlipperTarget(desktop, cache)
        if distribution == "alpine"
        else KlipperTarget(desktop, cache, distribution)
    )
libc = "glibc" if build_target == "gpu-2404" or klipper else build_target
if libc not in {"musl", "glibc", "kernel"}:
    raise ValueError("Unknown graphics libc")
script = (source / ("build-kernel.sh" if libc == "kernel" else "build-guest.sh")).read_text()
package_script = (source / "build-guest-packages.sh").read_text() if libc == "musl" else ""
inputs = (
    [
        source / "kernel.config",
        source / "generate-display-modes.py",
        desktop / "native/graphics/display/modes.json",
        desktop / "native/graphics/patches/kernel-virtio-vblank.patch",
        desktop / "native/graphics/patches/kernel-namespace-order.patch",
    ]
    if libc == "kernel"
    else [desktop / "native/graphics/patches/remote-proxy-fences.patch"]
)
input_names = {file: file.name for file in inputs}
component_runner = source / "build-component.sh"
inputs.append(component_runner)
input_names[component_runner] = component_runner.name
if libc != "kernel":
    pins = desktop / "native/graphics/sources.lock.json"
    inputs.append(pins)
    input_names[pins] = "sources.lock.json"
    bridge = desktop / "native/graphics/guest/gpu-bridge.c"
    inputs.append(bridge)
    input_names[bridge] = "guest/gpu-bridge.c"
    for directory in ("guest/driver", "patches/guest", "transport"):
        root = desktop / "native/graphics" / directory
        files = sorted(file for file in root.rglob("*") if file.is_file())
        if not files:
            raise RuntimeError(f"Missing guest graphics build inputs: {root}")
        for file in files:
            inputs.append(file)
            input_names[file] = str(Path(directory) / file.relative_to(root))
if libc == "glibc":
    script = "export SENTINEL_GRAPHICS_LIBC=glibc\n" + script
if libc == "musl":
    installer = desktop / "native/graphics/guest/install-native-mesa.py"
    inputs.append(installer)
    input_names[installer] = "guest/install-native-mesa.py"
    packaging = desktop / "native/graphics/packaging/mesa"
    for file in sorted(packaging.rglob("*")):
        if file.is_file() and "__pycache__" not in file.parts:
            inputs.append(file)
            input_names[file] = "packaging/mesa/" + str(file.relative_to(packaging))
    for dependency in ("labwc",):
        recipe = source / f"build-{dependency}.sh"
        inputs.append(recipe)
        input_names[recipe] = recipe.name
        root = desktop / "native/graphics/patches" / dependency
        files = sorted(file for file in root.rglob("*") if file.is_file())
        if not any(file.suffix == ".patch" for file in files):
            raise RuntimeError(f"Missing {dependency} build patches: {root}")
        for file in files:
            inputs.append(file)
            input_names[file] = f"patches/{dependency}/" + str(file.relative_to(root))
# Compile the shared glibc artifacts against the oldest supported ABI, not the
# latest workspace release. Both Linux and macOS builders use this exact image.
build_image = config["glibcBuildImage"] if libc == "glibc" else config["buildImage"]
if klipper:
    build_image = klipper.image
# Source edits change component keys, never the builder's OS disk identity.
builder_identity = json.dumps(
    {
        "schema": 1,
        "image": build_image,
        "kernel": config.get("kernelFileSha256"),
        "init": config.get("initImage"),
    },
    sort_keys=True,
)
builder_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "sentinel-graphics:" + builder_identity))
package_keys = {}
if libc == "musl":
    for name in ("labwc",):
        package_keys[name] = hashlib.sha256(
            component_runner.read_bytes()
            + build_image.encode()
            + json.dumps(json.loads(pins.read_text()).get(name), sort_keys=True).encode()
            + b"".join(
                input_names[file].encode() + b"\0" + file.read_bytes()
                for file in inputs
                if input_names[file] == f"build-{name}.sh"
                or input_names[file].startswith(f"patches/{name}/")
            )
        ).hexdigest()
key = hashlib.sha256(
    script.encode()
    + package_script.encode()
    + Path(__file__).read_bytes()
    + b"".join(input_names[file].encode() + b"\0" + file.read_bytes() + b"\0" for file in inputs)
    + json.dumps(config, sort_keys=True).encode()
).hexdigest()
artifact = cache / (key + ".tar.xz")
manifest = cache / (key + ".json")
if libc != "kernel":
    package_names = {"labwc"}
    core_inputs = [
        file
        for file in inputs
        if not any(
            input_names[file] == f"build-{name}.sh"
            or input_names[file].startswith(f"patches/{name}/")
            for name in package_names
        )
    ]
    # A desktop-package revision must not invalidate compiled Mesa/virgl.
    core_pins = json.loads(pins.read_text())
    for name in package_names:
        core_pins.pop(name, None)
    core_key = hashlib.sha256(
        script.encode()
        + json.dumps(core_pins, sort_keys=True).encode()
        + b"".join(
            input_names[file].encode() + b"\0" + file.read_bytes() + b"\0"
            for file in core_inputs
            if file != pins
        )
        + json.dumps(config, sort_keys=True).encode()
    ).hexdigest()
    core_artifact = cache / ("core-" + core_key + ".tar.xz")
    core_manifest = cache / ("core-" + core_key + ".json")


def sha(file):
    result = hashlib.sha256()
    with file.open("rb") as data:
        for chunk in iter(lambda: data.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def publish_file(source, target):
    """Keep verified targets untouched; never truncate an artifact being read."""
    try:
        if target.stat().st_size == source.stat().st_size and sha(target) == sha(source):
            return
    except FileNotFoundError:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".graphics-artifact-", dir=target.parent) as directory:
        staged = Path(directory) / target.name
        shutil.copy2(source, staged)
        staged.replace(target)


provider = None
if build_target == "gpu-2404":
    from gpu_provider import ProviderTarget

    provider = ProviderTarget(desktop, dest, cache, key)
    key, artifact, manifest = provider.key, provider.artifact, provider.manifest
if klipper:
    key = klipper.key


def core_cached():
    if libc != "musl":
        return False
    try:
        return json.loads(core_manifest.read_text())["sha256"] == sha(core_artifact)
    except (OSError, ValueError, KeyError):
        return False


def build_commands(output, guest_output=None):
    guest_output = Path(guest_output or output)
    if klipper:
        if klipper.distribution == "alpine":
            return klipper.commands(
                guest_output, test_user="sentinel-build", jobs=os.cpu_count() or 4
            )
        # Native DEB recipes require an ordinary account for their Qt regression.
        # Keep this bootstrap in the retained distro builder, never a workspace.
        return """set -eu
if ! command -v python3 >/dev/null || ! command -v useradd >/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update
    apt-get install -y --no-install-recommends python3 passwd ca-certificates
fi
id sentinel-build >/dev/null 2>&1 || useradd --create-home sentinel-build
""" + klipper.commands(guest_output, test_user="sentinel-build", jobs=os.cpu_count() or 4)
    if provider:
        return provider.commands(guest_output)
    setup = (
        "set -eu\nexport SENTINEL_GRAPHICS_BUNDLE=/var/cache/sentinel-build/bundles/"
        + key
        + '\nmkdir -p "$SENTINEL_GRAPHICS_BUNDLE"\n'
    )
    runner = shlex.quote(str(guest_output / "build-component.sh"))
    if libc != "musl":
        component_key = (
            core_key
            if libc == "glibc"
            else hashlib.sha256(
                script.encode()
                + build_image.encode()
                + b"".join(file.read_bytes() for file in inputs)
            ).hexdigest()
        )
        return (
            setup
            + f"sh {runner} {libc} {component_key} <<'SENTINEL_COMPONENT'\n"
            + script
            + "\nSENTINEL_COMPONENT\n"
        )
    core_output = Path(output) / "core.tar.xz"
    guest_core = guest_output / "core.tar.xz"
    if core_cached():
        shutil.copy2(core_artifact, core_output)
        core = 'tar -C "$SENTINEL_GRAPHICS_BUNDLE" -xJf ' + shlex.quote(str(guest_core))
    else:
        core = (
            f"sh {runner} core {core_key} <<'SENTINEL_COMPONENT'\n"
            + script
            + "\nSENTINEL_COMPONENT\n"
        )
        core += (
            'tar -C "$SENTINEL_GRAPHICS_BUNDLE" -cJf '
            + shlex.quote(str(guest_core) + ".next")
            + " .\n"
        )
        core += "mv " + shlex.quote(str(guest_core) + ".next") + " " + shlex.quote(str(guest_core))
    packages = "\n".join(
        f"export SENTINEL_{name.upper()}_KEY={value}" for name, value in package_keys.items()
    )
    return setup + core + "\n" + packages + "\n" + package_script


def record_core(output):
    if libc != "musl":
        return
    archive = Path(output) / "core.tar.xz"
    if archive.is_file() and not core_cached():
        publish_file(archive, core_artifact)
        core_manifest.write_text(
            json.dumps({"sha256": sha(core_artifact), "coreBuildKey": core_key}) + "\n"
        )


def record_artifact(archive):
    with tarfile.open(archive) as bundle:
        version = bundle.extractfile("./version").read().decode().strip()
    archive.replace(artifact)
    manifest.write_text(
        json.dumps(
            {
                "version": version,
                "sha256": sha(artifact),
                "buildKey": key,
                "inputs": {input_names[file]: sha(file) for file in inputs},
            }
        )
        + "\n"
    )


def cached_source(pin):
    # Called under the existing guest build lock; both libc builds share this cache.
    directory = cache / "sources"
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / pin["sha256"]
    if archive.exists() and sha(archive) == pin["sha256"]:
        print(f"Using cached source: {pin['url']}", flush=True)
        return archive
    partial = archive.with_suffix(".part")
    if not partial.exists() or sha(partial) != pin["sha256"]:
        print(f"Downloading source (resumable): {pin['url']}", flush=True)
        run(
            "curl",
            "--fail",
            "--location",
            "--retry",
            "2",
            "--connect-timeout",
            "15",
            "--speed-limit",
            "1024",
            "--speed-time",
            "60",
            "--continue-at",
            "-",
            "--output",
            partial,
            pin["url"],
        )
    if sha(partial) != pin["sha256"]:
        partial.unlink()
        raise RuntimeError(f"Source archive checksum mismatch: {pin['url']}")
    partial.replace(archive)
    return archive


def stage_inputs(output):
    if klipper:
        klipper.stage(Path(output))
        return
    if provider:
        provider.stage(Path(output), cached_source)
        return
    for file in inputs:
        target = Path(output) / input_names[file]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
    if libc != "kernel":
        pin = json.loads((desktop / "native/graphics/sources.lock.json").read_text())["mesa-guest"]
        shutil.copy2(cached_source(pin), Path(output) / "mesa.tar.xz")


def linux_build_script(commands):
    # Root is required for distro package installation, but the bind-mounted
    # temporary output belongs to the host runner. Restore it even on failure;
    # leave the persistent compiler cache alone. Do not follow output symlinks.
    return (
        "set -eu\n"
        "export PYTHONDONTWRITEBYTECODE=1\n"
        "export SENTINEL_GRAPHICS_INPUTS=/output\n"
        "restore_output_owner() {\n"
        "  status=$?\n"
        "  trap - EXIT\n"
        f"  chown -hR {os.getuid()}:{os.getgid()} /output || {{\n"
        '    if [ "$status" -eq 0 ]; then status=1; fi\n'
        "  }\n"
        '  exit "$status"\n'
        "}\n"
        "trap restore_output_owner EXIT\n"
        "(\n" + commands + "\n)\n"
    ).encode()


def build_linux():
    if platform.machine() not in {"aarch64", "arm64"}:
        raise RuntimeError("Guest graphics builds require a native Linux ARM64 host")
    # Containers are disposable; native compiler work trees must not be.
    # Use the same stable toolchain identity as the macOS builder, not a source
    # hash, so changed components can reuse ccache and failed builds can resume.
    build_cache = cache / "linux-builders" / builder_id
    build_cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="output-", dir=cache) as output:
        stage_inputs(output)
        commands = build_commands(output, "/output")
        run(
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/arm64",
            "--interactive",
            "--volume",
            f"{output}:/output",
            "--volume",
            f"{build_cache}:/var/cache/sentinel-build",
            "--entrypoint",
            "sh",
            build_image,
            "-s",
            input=linux_build_script(
                commands
                + (
                    ""
                    if provider or klipper
                    else '\ntar -C "$SENTINEL_GRAPHICS_BUNDLE" -cJf /output/graphics.tar.xz .\n'
                )
            ),
        )
        record_core(output)
        if klipper:
            klipper.record(Path(output), publish_file)
        elif provider:
            provider.record(Path(output), publish_file)
        else:
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

    workspace = builder_id
    builder_jobs = os.cpu_count() or 4
    output = cache / "builders" / builder_id / key
    output.mkdir(parents=True, exist_ok=True)
    with (
        (cache / f"build-{build_target}.log").open("w") as log,
        (Path(output) / "build-output.log").open("w+") as progress,
    ):
        stage_inputs(output)
        commands = build_commands(output)
        helper = subprocess.Popen(
            [
                str(runtime_helper),
                str(cache / "runtime"),
                str(kernel),
                config["initImage"],
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
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Graphics build helper did not reply in time")
                try:
                    reply = replies.get(timeout=min(1, remaining))
                except queue.Empty:
                    reply = None
                text = progress.read()
                if text:
                    log.write(text)
                    log.flush()
                    print(text, end="", flush=True)
                if reply is None:
                    continue
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
            return receive(identifier, timeout=max(1800, values.get("timeout", 0) + 60))

        ready = False
        started = False
        try:
            print(
                f"Using persistent graphics builder {builder_id} ({builder_jobs} cores)…",
                flush=True,
            )
            receive()
            ready = True
            request(
                "start",
                project=str(output),
                cpus=builder_jobs,
                memory_gib=min(
                    14,
                    max(
                        4,
                        int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True))
                        * 3
                        // (4 * 1024**3),
                    ),
                ),
                disk_gib=64,
                image_reference=build_image,
                **(
                    {"distribution": klipper.distribution}
                    if klipper
                    else {"distribution": "ubuntu"} if libc == "glibc" else {}
                ),
            )
            started = True
            archive = Path(output) / "graphics.tar.xz"
            result = request(
                "exec",
                arguments=[
                    "sh",
                    "-c",
                    "exec >"
                    + shlex.quote(progress.name)
                    + " 2>&1\nset -x\n"
                    + "export SENTINEL_GRAPHICS_INPUTS="
                    + shlex.quote(str(output))
                    + "\nexport SENTINEL_BUILD_JOBS="
                    + str(builder_jobs)
                    + "\n"
                    + commands
                    + (
                        ""
                        if provider or klipper
                        else '\ntar -C "$SENTINEL_GRAPHICS_BUNDLE" -cJf '
                        + shlex.quote(str(archive))
                        + " .\n"
                    ),
                ],
                timeout=3600,
            )
            log.write(result.get("stdout", "") + result.get("stderr", ""))
            log.flush()
            record_core(output)
            if result.get("exitCode") != 0:
                raise RuntimeError(
                    f'Desktop graphics build failed (exit {result.get("exitCode")}); see {cache / f"build-{build_target}.log"}: {result.get("stderr", "")[-2000:]}'
                )
            if klipper:
                klipper.record(Path(output), publish_file)
            elif provider:
                provider.record(Path(output), publish_file)
            else:
                record_artifact(archive)
        finally:
            try:
                # core.tar.xz is renamed atomically after completion. Save it
                # even if a later package times out or the connection fails.
                record_core(output)
                if ready and helper.poll() is None:
                    try:
                        if started:
                            flushed = request("exec", arguments=["sync"], timeout=30)
                            if flushed.get("exitCode") != 0:
                                raise RuntimeError("Could not flush the persistent build cache")
                    finally:
                        request("stop")
            finally:
                helper.stdin.close()
                try:
                    helper.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    helper.kill()
                    helper.wait()


if klipper:
    # Explicit native package targets share the normal VM/container transport.
    # They remain unqualified and are not included in runtime provisioning yet.
    supplied = os.environ.get("SENTINEL_GUEST_GRAPHICS_DIR")
    if os.environ.get("CI") and sys.platform == "darwin" and not supplied:
        raise RuntimeError("macOS CI requires SENTINEL_GUEST_GRAPHICS_DIR")
    with (cache / "build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        root = Path(supplied) / build_target if supplied else klipper.destination
        if supplied:
            klipper.verify(root)
        else:
            try:
                klipper.verify(root)
            except (OSError, ValueError, KeyError, tarfile.TarError):
                build_linux() if sys.platform == "linux" else build()
        for path in klipper.verify(root):
            publish_file(path, dest / build_target / path.relative_to(root))
    print(
        f"Verified native package target ready (not release-qualified): {build_target}",
        flush=True,
    )
    sys.exit(0)

bundle = (
    "gpu-2404"
    if provider
    else (
        "kernel-linux-arm64"
        if libc == "kernel"
        else "mesa-linux-arm64" + ("-glibc" if libc == "glibc" else "")
    )
)
extension = ".snap" if provider else ".tar.xz"
prebuilt = os.environ.get("SENTINEL_GUEST_GRAPHICS_DIR")
if prebuilt:
    supplied = Path(prebuilt)
    supplied_archive = supplied / (bundle + extension)
    supplied_manifest = supplied / (bundle + ".json")
    metadata = json.loads(supplied_manifest.read_text())
    valid = (
        provider.valid(supplied_archive, supplied_manifest)
        if provider
        else metadata.get("buildKey") == key and metadata.get("sha256") == sha(supplied_archive)
    )
    if not valid:
        raise RuntimeError(f"Guest graphics artifact does not match build inputs: {bundle}")
    dest.mkdir(parents=True, exist_ok=True)
    publish_file(supplied_archive, dest / supplied_archive.name)
    publish_file(supplied_manifest, dest / supplied_manifest.name)
    print(f"Verified prebuilt guest graphics: {bundle}", flush=True)
    sys.exit(0)
if os.environ.get("CI") and sys.platform == "darwin":
    raise RuntimeError("macOS CI requires SENTINEL_GUEST_GRAPHICS_DIR; guest VMs are not supported")

with (cache / "build.lock").open("w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        valid = (
            provider.valid(artifact, manifest)
            if provider
            else json.loads(manifest.read_text())["sha256"] == sha(artifact)
        )
    except (OSError, ValueError, KeyError):
        valid = False
    if not valid:
        if sys.platform == "linux":
            build_linux()
        else:
            build()
    dest.mkdir(parents=True, exist_ok=True)
    publish_file(artifact, dest / (bundle + extension))
    publish_file(manifest, dest / (bundle + ".json"))
    print("Precompiled desktop graphics bundle ready.", flush=True)
