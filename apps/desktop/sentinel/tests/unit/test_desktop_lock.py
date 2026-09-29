import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "desktop_lock", Path(__file__).resolve().parents[2] / "native/graphics/guest/desktop_lock.py"
)
lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock)


class DesktopLockTests(unittest.TestCase):
    def test_preferences_use_native_tools_as_user(self):
        with (
            patch.object(lock.os, "getuid", return_value=1001),
            patch.object(lock.subprocess, "run") as run,
        ):
            for desktop, count in (("plasma", 2), ("gnome", 2), ("xfce", 3), ("lxqt", 0)):
                run.reset_mock()
                lock.configure(desktop, {"HOME": "/home/sentinel"})
                self.assertEqual(run.call_count, count)
                for call in run.call_args_list:
                    self.assertTrue(call.kwargs["check"])
                    self.assertEqual(call.kwargs["env"]["HOME"], "/home/sentinel")

    def test_root_preferences_are_rejected(self):
        with patch.object(lock.os, "getuid", return_value=0), self.assertRaises(RuntimeError):
            lock.configure("plasma", {})
