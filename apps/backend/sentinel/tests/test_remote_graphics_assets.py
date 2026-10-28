"""The remote release must carry everything the locally built runtime needs to run."""

from pathlib import Path
import shutil
import subprocess

import pytest

from app.services.workspaces.machines.remote.worker import runtime_assets
from app.services.workspaces.machines.remote.release import REQUIRED

PACKAGES = Path(__file__).resolve().parents[4] / "packages"
SENTINELD, WORKSPACE = PACKAGES / "sentineld/build", PACKAGES / "sentinel-workspace/build"


@pytest.mark.skipif(
    not (SENTINELD / "bin/sentineld").exists()
    or not (WORKSPACE / "bin/sentinel-workspace").exists(),
    reason="the runtime is not built here (make -C packages/sentineld; make -C packages/sentinel-workspace)",
)
@pytest.mark.skipif(shutil.which("otool") is None, reason="needs macOS otool")
def test_release_carries_every_library_the_workspace_vm_loads():
    files = runtime_assets({"sentineld": str(SENTINELD), "workspace": str(WORKSPACE)})
    names = {name for _, name in files}
    assert REQUIRED <= names
    binaries = [
        WORKSPACE / "bin/sentinel-workspace",
        *sorted((WORKSPACE / "lib").glob("*.dylib")),
    ]
    for binary in binaries:
        linked = subprocess.run(
            ["otool", "-L", str(binary)], capture_output=True, text=True, check=True
        ).stdout.splitlines()[1:]
        for line in linked:
            library = line.split()[0]
            if library.startswith(("@rpath/", "@loader_path/", "@executable_path/")):
                bundled = "sentinel-workspace/lib/" + library.rsplit("/", 1)[1]
                assert bundled in names, (binary, library)
            else:
                assert library.startswith(("/usr/lib/", "/System/")), (binary, library)
    # The Vulkan ICD manifest names its driver relative to itself.
    assert "sentinel-workspace/lib/libvulkan_kosmickrisp.dylib" in names
    assert "sentinel-workspace/lib/kosmickrisp_icd.json" in names
