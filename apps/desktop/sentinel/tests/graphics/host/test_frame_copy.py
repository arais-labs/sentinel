"""Actual Metal frame-copy contract, independent of the Linux VM."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[3]


@unittest.skipUnless(sys.platform == "darwin", "Requires macOS Metal")
class MetalFrameCopyTests(unittest.TestCase):
    def test_pixels_ordering_backpressure_and_lifetime(self):
        with tempfile.TemporaryDirectory(prefix="sentinel-frame-copy-") as temporary:
            executable = Path(temporary) / "frame-copy"
            video = DESKTOP / "native/graphics/video"
            subprocess.run(
                [
                    "clang",
                    "-O2",
                    "-fobjc-arc",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I" + str(video),
                    str(Path(__file__).with_name("frame-copy.m")),
                    str(video / "MetalFrameCopy.m"),
                    "-framework",
                    "Foundation",
                    "-framework",
                    "Metal",
                    "-framework",
                    "CoreVideo",
                    "-framework",
                    "IOSurface",
                    "-o",
                    str(executable),
                ],
                check=True,
                timeout=60,
            )
            for validation in ("0", "1"):
                with self.subTest(metal_validation=validation):
                    environment = dict(
                        os.environ,
                        MTL_DEBUG_LAYER=validation,
                        MTL_SHADER_VALIDATION=validation,
                    )
                    result = subprocess.run(
                        [str(executable)],
                        capture_output=True,
                        text=True,
                        timeout=40,
                        env=environment,
                        check=False,
                    )
                    if result.returncode == 77:
                        self.skipTest("No Metal device available")
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("PASS 32 pixel cases", result.stdout)
                    self.assertIn("PASS cursor pixel composition", result.stdout)
                    self.assertIn("PASS exact three-inflight bound", result.stdout)
                    self.assertIn("PASS input rejection", result.stdout)
                    self.assertIn(
                        "PASS asynchronous copier/source/CVPixelBuffer lifetime",
                        result.stdout,
                    )


if __name__ == "__main__":
    unittest.main()
