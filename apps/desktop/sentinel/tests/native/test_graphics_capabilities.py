"""Validate the packaged GPU contract without booting a VM or adding delays."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DESKTOP / "tests"))
from metal_support import require_metal_device  # noqa: E402 - standalone unittest discovery


class GraphicsCapabilitiesTests(unittest.TestCase):
    def test_timer_queries_match_the_host_and_are_honored_by_mesa(self):
        require_metal_device(metal4=True)
        graphics = Path(
            os.environ.get(
                "SENTINEL_TEST_GRAPHICS_DIR",
                DESKTOP / "build/macos-arm64/runtime/workspace-runtime/graphics",
            )
        )
        renderer = graphics / "sentinel-desktop-renderer"
        self.assertTrue(renderer.is_file(), "Prepare the desktop runtime before native tests")
        work = DESKTOP / "build/graphics-sources"
        with tempfile.TemporaryDirectory(prefix="sentinel-capabilities-", dir="/tmp") as root:
            capset = Path(root) / "capset"
            probe = Path(root) / "probe"
            subprocess.run([str(renderer), str(capset), "--capabilities"], check=True, timeout=15)
            subprocess.run(
                [
                    "clang",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I" + str(work / "local/include"),
                    "-I" + str(work / "egl-registry/api"),
                    "-I" + str(work / "virgl/src"),
                    str(DESKTOP / "tests/native/graphics-capabilities.c"),
                    str(graphics / "libepoxy.0.dylib"),
                    "-Wl,-rpath," + str(graphics),
                    "-o",
                    str(probe),
                ],
                check=True,
                timeout=30,
            )
            subprocess.run(
                [
                    "install_name_tool",
                    "-change",
                    "@loader_path/libepoxy.0.dylib",
                    "@rpath/libepoxy.0.dylib",
                    str(probe),
                ],
                check=True,
                timeout=15,
            )
            subprocess.run(
                [str(probe), str(capset)],
                check=True,
                timeout=15,
                env={
                    **os.environ,
                    "VK_DRIVER_FILES": str(graphics / "icd.json"),
                    "MESA_LOADER_DRIVER_OVERRIDE": "zink",
                },
            )
