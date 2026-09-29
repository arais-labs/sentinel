import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / "native/graphics/guest/sentinel_display.py"
spec = importlib.util.spec_from_file_location("sentinel_display", source)
display = importlib.util.module_from_spec(spec)
spec.loader.exec_module(display)


class ClipboardReadTests(unittest.TestCase):
    def run_child(self, code):
        clipboard = display.Clipboard()
        clipboard.session = lambda: (
            "wayland",
            {"WAYLAND_DISPLAY": "actual-display", "XDG_RUNTIME_DIR": "/run/user/1234"},
        )
        clipboard.credentials = dict
        popen = subprocess.Popen
        self.children = []

        def start(_command, **options):
            options.pop("env")
            process = popen([sys.executable, "-c", code], **options)
            self.children.append(process)
            return process

        with patch.object(display.subprocess, "Popen", side_effect=start):
            return clipboard.read()

    def tearDown(self):
        for child in getattr(self, "children", []):
            self.assertIsNotNone(child.poll(), "clipboard reader leaked a child")

    def test_exact_unicode_and_empty_selection(self):
        text = "Bonjour 世界 👋\n"
        self.assertEqual(self.run_child(f"import sys;sys.stdout.write({text!r})"), text)
        self.assertEqual(self.run_child("raise SystemExit(1)"), "")

    def test_unclosed_pipe_has_one_bounded_deadline(self):
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_child("import os,signal;os.write(1,b'partial');signal.pause()")
        self.assertLess(time.monotonic() - started, 8)

    def test_oversized_selection_terminates_writer(self):
        with self.assertRaisesRegex(ValueError, "256 KiB"):
            self.run_child("import os,signal;os.write(1,b'x'*400000);signal.pause()")

    def test_exact_limit_is_accepted(self):
        self.assertEqual(len(self.run_child("import sys;sys.stdout.write('x'*262144)")), 262144)

    def test_unknown_protocol_does_not_guess_x11(self):
        clipboard = display.Clipboard()
        clipboard.session = lambda: ("unknown", {})
        with self.assertRaisesRegex(RuntimeError, "clipboard transfer"):
            clipboard.read()


class ClipboardCredentialsTests(unittest.TestCase):
    def test_root_supervisor_uses_published_regular_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            path.write_text(
                json.dumps(
                    {
                        "protocol": "wayland",
                        "clipboard_backend": "wayland",
                        "environment": {"HOME": "/home/sentinel"},
                        "user": {"uid": 1234, "gid": 1234, "groups": [1234, 44]},
                    }
                )
            )
            clipboard = display.Clipboard(path)
            with patch.object(display.os, "geteuid", return_value=0):
                self.assertEqual(
                    clipboard.credentials(),
                    {"user": 1234, "group": 1234, "extra_groups": [1234, 44]},
                )
            with patch.object(display.os, "geteuid", return_value=1234):
                self.assertEqual(clipboard.credentials(), {})
            with (
                patch.object(display.os, "geteuid", return_value=4321),
                self.assertRaisesRegex(RuntimeError, "another user"),
            ):
                clipboard.credentials()
            self.assertEqual(clipboard.session()[1]["HOME"], "/home/sentinel")

    def test_root_desktop_metadata_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            clipboard = display.Clipboard(path)
            for uid, gid in ((0, 1234), (1234, 0)):
                with self.subTest(uid=uid, gid=gid):
                    path.write_text(json.dumps({"user": {"uid": uid, "gid": gid, "groups": []}}))
                    with self.assertRaisesRegex(RuntimeError, "regular user"):
                        clipboard.credentials()


class ClipboardBackendTests(unittest.TestCase):
    def test_wayland_uses_bundled_protocol_aware_tools(self):
        clipboard = display.Clipboard()
        environment = {"WAYLAND_DISPLAY": "actual-display", "XDG_RUNTIME_DIR": "/run/user/1234"}
        self.assertEqual(
            clipboard.command("wayland", environment, write=True),
            [
                "/opt/sentinel/graphics/bin/wl-copy",
                "--type",
                "text/plain;charset=utf-8",
            ],
        )
        self.assertEqual(
            clipboard.command("wayland", environment, write=False),
            [
                "/opt/sentinel/graphics/bin/wl-paste",
                "--no-newline",
                "--type",
                "text",
            ],
        )

    def test_x11_requires_actual_authentication_even_for_wayland_session(self):
        clipboard = display.Clipboard()
        for environment in ({}, {"DISPLAY": ":71"}, {"XAUTHORITY": "/run/user/1234/auth"}):
            with self.assertRaisesRegex(RuntimeError, "authenticated"):
                clipboard.command("x11", environment, write=False)
        environment = {"DISPLAY": ":71", "XAUTHORITY": "/run/user/1234/auth"}
        self.assertEqual(
            clipboard.command("x11", environment, write=False),
            [
                "/usr/bin/xclip",
                "-selection",
                "clipboard",
                "-out",
            ],
        )

    def test_explicit_backend_not_protocol_or_callers_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            path.write_text(
                json.dumps(
                    {
                        "protocol": "wayland",
                        "clipboard_backend": "x11",
                        "environment": {"HOME": "/home/sentinel"},
                    }
                )
            )
            with patch.dict(display.os.environ, {"DISPLAY": ":99", "XAUTHORITY": "/wrong/auth"}):
                backend, environment = display.Clipboard(path).session()
            self.assertEqual(backend, "x11")
            self.assertNotIn("DISPLAY", environment)
            with self.assertRaisesRegex(RuntimeError, "authenticated"):
                display.Clipboard(path).command(backend, environment, write=False)

    def test_wayland_does_not_fall_back_to_available_x11(self):
        with self.assertRaisesRegex(RuntimeError, "Wayland clipboard"):
            display.Clipboard().command(
                "wayland", {"DISPLAY": ":71", "XAUTHORITY": "/auth"}, write=True
            )
