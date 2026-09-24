"""Build the privately bundled Metal renderer. Never installs host packages."""

import fcntl
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from host import build_host, bundle_identity, apple_environment_identity, LIBRARIES
from artifacts import publish_directory
from guest_assets import publish_session_assets, publish_kernel
from klipper import AlpineKlipperTarget, KlipperTarget

os.environ.setdefault("SENTINEL_BUILD_JOBS", str(os.cpu_count() or 4))
source = Path(__file__).resolve().parent
desktop = source.parents[2]
work = desktop / "build/graphics-sources"
work.mkdir(parents=True, exist_ok=True)
# make dev and an explicit packaging build may target the same generated bundle.
build_lock = (work / "build.lock").open("w")
try:
    fcntl.flock(build_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("Another graphics build holds the build lock; waiting for it to finish...", flush=True)
    fcntl.flock(build_lock, fcntl.LOCK_EX)
    print("Graphics build lock acquired; continuing.", flush=True)
dest = (
    Path(sys.argv[1]).resolve()
    if len(sys.argv) > 1
    else desktop / "build/macos-arm64/runtime/workspace-runtime/graphics"
)
runtime_helper = dest.parent / "sentinel-workspace-runtime"
environment = apple_environment_identity()
stamp = bundle_identity(desktop, environment)
required = [
    "sentinel-desktop-renderer",
    *LIBRARIES,
    "icd.json",
    "Mesa-LICENSE",
]


def prepare_guest_assets():
    # Native packages and corresponding source are shipped inside the desktop
    # archive, not duplicated as loose files beside it in the host runtime.
    native_assets = work / "native-package-assets"
    for target in (
        "musl",
        "glibc",
        "gpu-2404",
        "kernel",
        "klipper-alpine",
        "klipper-debian",
        "klipper-ubuntu",
    ):
        subprocess.run(
            [
                sys.executable,
                str(source / "build-guest.py"),
                str(native_assets if target.startswith("klipper-") else dest),
                target,
                str(runtime_helper),
            ],
            check=True,
        )
    # Ship the tested kernel, not a second download at workspace startup.
    publish_kernel(dest)
    packages = []
    for distribution in ("alpine", "debian", "ubuntu"):
        target = (
            AlpineKlipperTarget(desktop, work / "guest")
            if distribution == "alpine"
            else KlipperTarget(desktop, work / "guest", distribution)
        )
        root = native_assets / ("klipper-" + distribution)
        packages.append((distribution, root, target.verify(root)))
    publish_session_assets(desktop / "native/graphics/guest", dest, packages)


if (
    (dest / "stamp").exists()
    and (dest / "stamp").read_text() == stamp
    and all((dest / name).is_file() for name in required)
):
    prepare_guest_assets()
    sys.exit(0)
work.mkdir(parents=True, exist_ok=True)
published = dest
published.parent.mkdir(parents=True, exist_ok=True)
staged_bundle = tempfile.TemporaryDirectory(prefix=".graphics-build-", dir=published.parent)
dest = Path(staged_bundle.name) / "graphics"
build_host(desktop, work, dest, environment)


def run(*args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


run(sys.executable, source / "build-desktop.py", work, dest, dest / "sentinel-desktop-renderer")
prepare_guest_assets()
(dest / "stamp").write_text(stamp)
publish_directory(dest, published)
for name in ("kernel", "kernel-manifest.json"):
    (Path(staged_bundle.name) / name).replace(published.parent / name)
staged_bundle.cleanup()
print("Bundled Metal renderer ready:", published)
