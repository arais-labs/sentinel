"""Deterministic backpressure oracle; frame-ring covers real H264 output."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform == "darwin", "Requires macOS VideoToolbox")
class VideoEncoderTests(unittest.TestCase):
    def test_completion_delivers_pending_latest_frame(self):
        source = Path(__file__).with_name("encoder-backpressure.m")
        native = source.parents[3] / "native/graphics/video"
        with tempfile.TemporaryDirectory(prefix="sentinel-encoder-test-") as temporary:
            executable = Path(temporary) / "encoder-backpressure"
            subprocess.run(
                [
                    "clang",
                    "-O2",
                    "-fobjc-arc",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I" + str(native),
                    str(source),
                    "-o",
                    str(executable),
                    *(
                        flag
                        for name in (
                            "Foundation",
                            "VideoToolbox",
                            "CoreVideo",
                            "CoreMedia",
                            "QuartzCore",
                        )
                        for flag in ("-framework", name)
                    ),
                ],
                check=True,
                timeout=60,
            )
            result = subprocess.run(
                [str(executable)], capture_output=True, text=True, timeout=20, check=False
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS encoder bounded pending-latest", result.stdout)
