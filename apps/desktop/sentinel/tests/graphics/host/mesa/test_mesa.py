import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tests"))
from metal_support import require_metal_device  # noqa: E402 - standalone unittest discovery


class HostMesaTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "host Metal requires macOS")
    def test_hardware_capabilities_mip_views_and_buffer_storage(self):
        require_metal_device()
        explicit = os.environ.get("SENTINEL_TEST_GRAPHICS_DIR")
        libraries = (
            Path(explicit).resolve()
            if explicit
            else (ROOT / "build/macos-arm64/runtime/workspace-runtime/graphics")
        )
        sources = Path(
            os.environ.get(
                "SENTINEL_TEST_GRAPHICS_SOURCE_DIR", str(ROOT / "build/graphics-sources")
            )
        ).resolve()
        required = [
            libraries / "libEGL.dylib",
            libraries / "libepoxy.0.dylib",
            libraries / "icd.json",
            sources / "mesa-host/include/EGL/egl.h",
            sources / "epoxy/include/epoxy/egl.h",
            sources / "epoxy-build/include/epoxy/egl_generated.h",
            sources / "mesa-host/src/gallium/drivers/zink/zink_buffer_ranges.h",
            sources / "mesa-zink-build/src/util/format/u_format_gen.h",
            sources / "mesa-host/src/util/tests/queue_finish_test.c",
            sources / "mesa-zink-build/compile_commands.json",
            sources / "mesa-zink-build/src/util/libmesa_util.a",
            sources / "mesa-zink-build/src/c11/impl/libmesa_util_c11.a",
        ]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            self.fail("Build required host graphics artifacts first: " + ", ".join(missing))
        output = Path(
            os.environ.get(
                "SENTINEL_TEST_GRAPHICS_BUILD_DIR", str(ROOT / "build/graphics-tests/host-mesa")
            )
        ).resolve()
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("run.py")),
                "--mesa-source",
                str(sources / "mesa-host"),
                "--mesa-build",
                str(sources / "mesa-zink-build"),
                "--epoxy-source",
                str(sources / "epoxy"),
                "--epoxy-build",
                str(sources / "epoxy-build"),
                "--library-dir",
                str(libraries),
                "--icd",
                str(libraries / "icd.json"),
                "--build-dir",
                str(output),
                "--repetitions",
                "3",
            ],
            check=True,
            timeout=600,
        )


if __name__ == "__main__":
    unittest.main()
