import os
import subprocess
import sys
import unittest
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[2]


@unittest.skipUnless(sys.platform == "darwin", "Requires the macOS runtime")
class RuntimeSignalsTests(unittest.TestCase):
    def test_closed_output_pipe_does_not_kill_runtime_with_sigpipe(self):
        runtime = Path(
            os.environ.get(
                "SENTINEL_TEST_WORKSPACE_RUNTIME",
                DESKTOP / "build/macos-arm64/runtime/workspace-runtime/sentinel-workspace-runtime",
            )
        )
        self.assertTrue(runtime.is_file(), "Prepare the workspace runtime before native tests")
        for arguments in ([], ["--service"], ["--owned-service"]):
            with self.subTest(arguments=arguments):
                reader, writer = os.pipe()
                os.close(reader)
                try:
                    result = subprocess.run(
                        [str(runtime), *arguments],
                        stdin=subprocess.DEVNULL,
                        stdout=writer,
                        stderr=subprocess.PIPE,
                        timeout=15,
                        text=True,
                        check=False,
                    )
                finally:
                    os.close(writer)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("Expected: sentinel-workspace-runtime", result.stderr)


if __name__ == "__main__":
    unittest.main()
