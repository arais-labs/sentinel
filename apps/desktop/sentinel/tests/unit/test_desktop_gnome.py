import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / "native/graphics/guest/desktop_gnome.py"
spec = importlib.util.spec_from_file_location("desktop_gnome_test", source)
gnome = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gnome)


class DesktopGnomeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = {
            "command": ["/usr/bin/gnome-session"],
            "session_manager": "native",
            "environment": {"CUSTOM": "preserved"},
            "audio": False,
        }
        self.write(
            "usr/lib/systemd/user/org.gnome.Shell@wayland.service",
            "[Service]\nExecStart=/usr/bin/gnome-shell\n",
        )
        self.write(
            "usr/lib/systemd/user/org.gnome.Shell@.service",
            "[Service]\nExecStart=/usr/bin/gnome-shell --mode=%i\n",
        )
        for name in (
            "gnome-session@.target",
            "gnome-session-services.target",
            "gnome-session-manager@.service",
        ):
            self.write("usr/lib/systemd/user/" + name, "[Unit]\n")
        self.write("usr/share/applications/org.gnome.Shell.desktop", "Exec=/usr/bin/gnome-shell\n")
        self.write("home/sentinel/.config/dconf/user", "user preferences")

    def write(self, relative, contents):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)

    def install(self, session=48, shell=50):
        with patch.object(gnome, "_major", side_effect=[session, shell]):
            return gnome.configure_gnome(self.config, self.root)

    def test_supported_native_lifecycles_preserve_preferences_and_stock_services(self):
        for session, shell, systemd in ((48, 48, True), (48, 50, False), (50, 50, True)):
            with self.subTest(session=session, shell=shell, systemd=systemd):
                init = self.root / "run/systemd/system"
                if systemd:
                    init.mkdir(parents=True, exist_ok=True)
                elif init.exists():
                    init.rmdir()
                stock = {
                    path: path.read_bytes()
                    for path in (self.root / "usr/lib/systemd/user").iterdir()
                }
                result = self.install(session, shell)
                self.assertEqual(result["environment"]["CUSTOM"], "preserved")
                self.assertEqual(result["environment"]["GNOME_SHELL_SESSION_MODE"], "sentinel")
                self.assertFalse(result["audio"])
                self.assertNotIn("GNOME_SHELL_SESSION_MODE", self.config["environment"])
                expected = "sentinel" if session == 50 else "gnome"
                self.assertEqual(
                    result["command"], ["/usr/bin/gnome-session", "--session=" + expected]
                )
                self.assertTrue(all(path.read_bytes() == value for path, value in stock.items()))
                self.assertEqual(
                    (self.root / "home/sentinel/.config/dconf/user").read_text(), "user preferences"
                )
                mode = self.root / "usr/local/share/gnome-shell/modes/sentinel.json"
                self.assertEqual(
                    json.loads(mode.read_text()),
                    {"parentMode": "user", "enabledExtensions": [gnome.UUID]},
                )
                self.assertEqual(mode.stat().st_mode & 0o777, 0o644)
                extension = self.root / "usr/local/share/gnome-shell/extensions" / gnome.UUID
                self.assertEqual(
                    (extension / "extension.js").read_bytes(),
                    (gnome.ASSETS / "extension.js").read_bytes(),
                )
                if session == 50:
                    target = (
                        self.root / "etc/systemd/user/gnome-session@sentinel.target.d/session.conf"
                    )
                    self.assertEqual(
                        target.read_text(),
                        "[Unit]\nRequires=gnome-session-services.target\nRequires=org.gnome.Shell@sentinel.service\n",
                    )

    def test_custom_command_or_mode_is_rejected_without_files_or_preference_changes(self):
        for command, mode in (
            (["/custom/gnome-session"], "user"),
            (["/usr/bin/gnome-session"], "custom-mode"),
        ):
            self.config["command"] = command
            self.config["environment"]["GNOME_SHELL_SESSION_MODE"] = mode
            with self.assertRaisesRegex(RuntimeError, "custom"):
                self.install()
            self.assertFalse((self.root / "usr/local/share").exists())

    def test_unreviewed_versions_and_session50_without_systemd_fail_before_writes(self):
        for session, shell in ((49, 50), (48, 51), (50, 50)):
            with self.subTest(session=session, shell=shell), self.assertRaises(RuntimeError):
                self.install(session, shell)
            self.assertFalse((self.root / "usr/local/share").exists())

    def test_mode_override_or_missing_stock_dependency_is_not_silently_replaced(self):
        (self.root / "run/systemd/system").mkdir(parents=True)
        self.write(
            "usr/lib/systemd/user/org.gnome.Shell@.service",
            "[Service]\nExecStart=/usr/bin/gnome-shell --mode=user\n",
        )
        with self.assertRaisesRegex(RuntimeError, "instance-selected"):
            self.install(50)
        self.assertFalse((self.root / "usr/local/share").exists())
        self.write(
            "usr/lib/systemd/user/org.gnome.Shell@.service",
            "[Service]\nExecStart=/usr/bin/gnome-shell --mode=%i\n",
        )
        (self.root / "usr/lib/systemd/user/gnome-session-services.target").unlink()
        with self.assertRaisesRegex(RuntimeError, "stock native unit"):
            self.install(50)
        self.assertFalse((self.root / "usr/local/share").exists())

    def test_existing_shell_dropin_requires_review(self):
        (self.root / "run/systemd/system").mkdir(parents=True)
        self.write("etc/systemd/user/org.gnome.Shell@wayland.service.d/custom.conf", "[Service]\n")
        with self.assertRaisesRegex(RuntimeError, "custom"):
            self.install(48)
        self.assertFalse((self.root / "usr/local/share").exists())


if __name__ == "__main__":
    unittest.main()
