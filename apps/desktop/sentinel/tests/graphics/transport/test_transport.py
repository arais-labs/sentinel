"""Portable actual-socket regressions; no VM or graphics API required."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TRANSPORT = ROOT / "native/graphics/transport"


def sanitizer_flags():
    return (
        ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        if os.environ.get("SENTINEL_TRANSPORT_SANITIZERS") == "1"
        else []
    )


@unittest.skipUnless(shutil.which("cc"), "A C11 compiler is required")
class CreditTransportTests(unittest.TestCase):
    def test_continuous_credit_scheduler_fairness(self):
        with tempfile.TemporaryDirectory(prefix="sentinel-credit-fairness-") as directory:
            executable = Path(directory) / "fairness-test"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    *sanitizer_flags(),
                    "-I",
                    str(TRANSPORT),
                    str(Path(__file__).with_name("fairness_test.c")),
                    "-o",
                    str(executable),
                ],
                check=True,
                timeout=30,
            )
            subprocess.run([str(executable)], check=True, timeout=10)

    def test_exact_bytes_backpressure_protocol_and_cancellation(self):
        with tempfile.TemporaryDirectory(prefix="sentinel-credit-test-") as directory:
            executable = Path(directory) / "transport-test"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-pthread",
                    *sanitizer_flags(),
                    "-I",
                    str(TRANSPORT),
                    str(TRANSPORT / "gpu_transport.c"),
                    str(Path(__file__).with_name("transport_test.c")),
                    "-o",
                    str(executable),
                ],
                check=True,
                timeout=30,
            )
            result = subprocess.run(
                [str(executable)],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("ALL GPU CREDIT TRANSPORT TESTS PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
