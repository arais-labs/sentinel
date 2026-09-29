"""The virtual GPU selection reaches login and non-login consumers."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "graphics_environment", ROOT / "native/graphics/guest/graphics_environment.py"
)
gpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpu)


class GraphicsEnvironmentTests(unittest.TestCase):
    def test_install_is_repeatable_and_matches_non_login_exec(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gpu.install(root)
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*.conf")}
            gpu.install(root)
            self.assertEqual(
                before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*.conf")}
            )
            key, value = next(iter(gpu.GPU_ENVIRONMENT.items()))
            self.assertIn(
                f"{key}={value}", (root / "etc/environment.d/60-sentinel-gpu.conf").read_text()
            )
            self.assertIn(
                f"export {key}='{value}'", (root / "etc/profile.d/sentinel-gpu.sh").read_text()
            )
            self.assertIn(
                f"{key}={value}",
                (root / "etc/systemd/system.conf.d/60-sentinel-gpu.conf").read_text(),
            )
            self.assertIn(
                f'"{key}={value}"',
                (ROOT / "native/macos/Sources/WorkspaceRuntime/GuestRequests.swift").read_text(),
            )
