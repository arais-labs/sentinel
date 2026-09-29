import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / "native/graphics/guest/desktop_keyboard_native.py"
spec = importlib.util.spec_from_file_location("desktop_keyboard_native", source)
native = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(source.parent), *sys.path]):
    spec.loader.exec_module(native)


class NativeKeyboardTests(unittest.TestCase):
    def setUp(self):
        self.keyboard = {
            "layout": "layout_one,layout_two",
            "variant": ",variant_two",
            "model": "test_model",
            "options": "grp:test,compose:test,custom:option",
        }
        self.environment = {
            "HOME": "/home/desktop",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1001/bus",
        }

    def commands(self, desktop):
        with (
            patch.object(native.os, "getuid", return_value=1001),
            patch.object(native.os, "geteuid", return_value=1001),
            patch.object(native, "run") as run,
        ):
            native.before_start(desktop, self.keyboard, self.environment)
        for call in run.call_args_list:
            self.assertEqual(call.args[1], self.environment)
        return [call.args[0] for call in run.call_args_list]

    def test_gnome_uses_native_input_sources_models_options_before_shell(self):
        commands = self.commands("gnome")
        settings = {command[3]: command[4] for command in commands}
        self.assertTrue(
            all(
                command[:3] == ["gsettings", "set", "org.gnome.desktop.input-sources"]
                for command in commands
            )
        )
        self.assertEqual(
            settings["sources"],
            "[('xkb', 'layout_one'), ('xkb', 'layout_two+variant_two')]",
        )
        self.assertEqual(settings["mru-sources"], settings["sources"])
        self.assertEqual(settings["xkb-model"], "'test_model'")
        self.assertIn("'custom:option'", settings["xkb-options"])
        self.assertNotIn("current", settings)  # Deprecated GNOME setting.

    def test_plasma_writes_only_native_keyboard_group(self):
        commands = self.commands("KDE")
        self.assertTrue(
            all(
                command[:5] == ["kwriteconfig6", "--file", "kxkbrc", "--group", "Layout"]
                for command in commands
            )
        )
        settings = {command[6]: command[7] for command in commands}
        self.assertEqual(
            settings,
            {
                "Model": "test_model",
                "LayoutList": "layout_one,layout_two",
                "VariantList": ",variant_two",
                "Options": "grp:test,compose:test,custom:option",
                "ResetOldOptions": "true",
                "Use": "true",
            },
        )

    def test_xfce_sets_native_preferences_and_complete_authenticated_xkb_map(self):
        commands = self.commands("xfce")
        settings = {command[4]: command[-1] for command in commands}
        self.assertEqual(settings["/Default/XkbLayout"], "layout_one,layout_two")
        self.assertEqual(settings["/Default/XkbVariant"], ",variant_two")
        self.assertEqual(settings["/Default/XkbOptions/Group"], "grp:test")
        self.assertEqual(settings["/Default/XkbOptions/Compose"], "compose:test")
        self.assertEqual(settings["/Default/XkbDisable"], "false")
        credentials = {"user": 1001, "group": 1001, "extra_groups": [44]}
        environment = {
            **self.environment,
            "DISPLAY": ":7",
            "XAUTHORITY": "/home/desktop/.Xauthority",
        }
        with patch.object(native, "run") as run:
            native.after_start("xfce", self.keyboard, environment, credentials)
        run.assert_called_once_with(
            [
                "setxkbmap",
                "-rules",
                "evdev",
                "-model",
                "test_model",
                "-layout",
                "layout_one,layout_two",
                "-variant",
                ",variant_two",
                "-option",
                "",
                "-option",
                "grp:test,compose:test,custom:option",
            ],
            environment,
            credentials,
        )

    def test_lxqt_keeps_compositor_native_environment_path(self):
        self.assertEqual(self.commands("lxqt"), [])

    def test_explicit_null_never_modifies_native_preferences(self):
        for desktop in ("xfce", "gnome", "plasma", "lxqt"):
            with self.subTest(desktop=desktop), patch.object(native, "run") as run:
                native.before_start(desktop, None, {})
                native.after_start(desktop, None, {}, {})
                run.assert_not_called()

    def test_root_cannot_write_normal_desktop_keyboard_preferences(self):
        with (
            patch.object(native.os, "getuid", return_value=0),
            patch.object(native, "run") as run,
            self.assertRaisesRegex(RuntimeError, "desktop user"),
        ):
            native.before_start("gnome", self.keyboard, self.environment)
        run.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "credentials"):
            native.after_start("xfce", self.keyboard, {}, {"user": 0})

    def test_gnome_custom_rules_fail_before_any_partial_preferences_write(self):
        with (
            patch.object(native.os, "getuid", return_value=1001),
            patch.object(native.os, "geteuid", return_value=1001),
            patch.object(native, "run") as run,
            self.assertRaisesRegex(ValueError, "rules"),
        ):
            native.before_start("gnome", {**self.keyboard, "rules": "custom"}, self.environment)
        run.assert_not_called()

    def test_commands_have_no_shell_and_bounded_timeout(self):
        with patch.object(native.subprocess, "run") as run:
            native.run(["tool", "argument"], self.environment, {"user": 1001})
        self.assertEqual(run.call_args.args[0], ["tool", "argument"])
        self.assertEqual(run.call_args.kwargs["timeout"], 8)
        self.assertEqual(run.call_args.kwargs["user"], 1001)
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertNotIn("shell", run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
