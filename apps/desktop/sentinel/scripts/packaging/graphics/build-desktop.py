"""Build the worker-owned virtual GPU display against the bundled graphics libs."""

import os
import json
import shutil
import subprocess
import sys
from pathlib import Path

from upstream import prepare_source
from host import apple_environment_identity

REVISION = "1336f8af5fa9c1f4a7c55dd6ba94990de50e739e"


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def build(work, libraries, output):
    desktop = Path(__file__).resolve().parents[3]
    native = desktop / "native/graphics"
    upstream = prepare_source(
        work,
        "unified-hmi/remote-virtio-gpu",
        REVISION,
        "remote-virtio-gpu",
        [native / "patches/host/remote-renderer.patch"],
    )
    objects = work / "desktop-renderer"
    venv = work / "venv/bin"
    environment = apple_environment_identity()
    environment_key = json.dumps(environment, sort_keys=True)
    environment_stamp = objects / ".sentinel-environment"
    env = {
        **os.environ,
        "PATH": str(venv) + os.pathsep + os.environ.get("PATH", ""),
        "CC": "/usr/bin/clang",
        "OBJC": "/usr/bin/clang",
        "SDKROOT": environment["sdkPath"],
    }
    configured = (objects / "meson-private/coredata.dat").is_file()
    # Meson retains compiler discovery, and Ninja cannot detect an in-place
    # SDK/compiler update from an unchanged command line. Wipe only on an
    # environment change; ordinary source edits retain incremental objects.
    same_environment = (
        environment_stamp.is_file() and environment_stamp.read_text() == environment_key
    )
    mode = ["--reconfigure" if same_environment else "--wipe"] if configured else []
    run(
        venv / "meson",
        "setup",
        *mode,
        objects,
        native,
        "-Dgraphics_work=" + str(work),
        "-Dgraphics_libraries=" + str(libraries),
        env=env,
    )
    run(venv / "ninja", "-C", objects, "-j" + os.environ.get("SENTINEL_BUILD_JOBS", "4"), env=env)
    environment_stamp.write_text(environment_key)
    dependencies = [
        "libvirglrenderer.1.dylib",
        "libepoxy.0.dylib",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(objects / "sentinel-desktop-renderer", output)
    linked = subprocess.check_output(["otool", "-L", str(output)], text=True).splitlines()[1:]
    for line in linked:
        dependency = line.strip().split(" (", 1)[0]
        if Path(dependency).name in dependencies:
            run(
                "install_name_tool",
                "-change",
                dependency,
                "@rpath/" + Path(dependency).name,
                output,
            )
    run("codesign", "--force", "--sign", "-", output)
    shutil.copy2(upstream / "LICENSE.md", output.parent / "remote-virtio-gpu-LICENSE")


if __name__ == "__main__":
    build(*(Path(value).resolve() for value in sys.argv[1:]))
