import importlib.util
import ctypes
from pathlib import Path
import sys
import unittest

source = Path(__file__).resolve().parents[2] / "native/graphics/guest/desktop_keyboard.py"
spec = importlib.util.spec_from_file_location("desktop_keyboard", source)
keyboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keyboard)


class KeyboardConfigurationTests(unittest.TestCase):
    def setUp(self):
        # Synthetic configuration: behavior must not depend on a named layout.
        self.host = {"layout": "test_layout", "variant": "test_variant"}

    def test_guest_override_and_explicit_opt_out(self):
        request = {"keyboard": self.host}
        self.assertEqual(keyboard.resolve_keyboard({}, request)["layout"], "test_layout")
        self.assertEqual(
            keyboard.resolve_keyboard({"keyboard": {"layout": "custom"}}, request)["layout"],
            "custom",
        )
        self.assertIsNone(keyboard.resolve_keyboard({"keyboard": None}, request))
        self.assertIsNone(keyboard.resolve_keyboard({}, {}))

    def test_configuration_cannot_inject_xorg_or_ini_directives(self):
        for invalid in (
            {},
            [],
            {"layout": "x\n[core]"},
            {"layout": 'x"'},
            {"layout": "x", "unknown": "x"},
            {"layout": 4},
            {"layout": "x" * 257},
        ):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                keyboard.resolve_keyboard({"keyboard": invalid}, {})

    def test_both_desktops_receive_the_same_xkb_fields(self):
        resolved = keyboard.resolve_keyboard({}, {"keyboard": self.host})
        xorg = keyboard.xorg_keyboard(resolved)
        weston = keyboard.weston_keyboard("[core]\nxwayland=true\n", resolved)
        for field, value in resolved.items():
            self.assertIn(f'Option "Xkb{field.title()}" "{value}"', xorg)
            self.assertIn(f"keymap_{field}={value}\n", weston)
        self.assertEqual(keyboard.weston_keyboard(weston, resolved), weston)

    def test_weston_preserves_launchers_and_keyboard_preferences(self):
        resolved = keyboard.resolve_keyboard({}, {"keyboard": self.host})
        saved = "[launcher]\npath=one\n[keyboard]\nrepeat-rate=25\n[launcher]\npath=two\n"
        result = keyboard.weston_keyboard(saved, resolved)
        self.assertIn("repeat-rate=25\n", result)
        self.assertEqual(result.count("[keyboard]"), 1)
        self.assertEqual(result.count("[launcher]"), 2)
        configured = "[keyboard]\nkeymap_layout=custom\n[launcher]\npath=one\n"
        self.assertEqual(keyboard.weston_keyboard(configured, resolved), configured)
        self.assertEqual(keyboard.weston_effective_keyboard(configured)["layout"], "custom")
        self.assertEqual(keyboard.weston_effective_keyboard(result), resolved)

    def test_opt_out_leaves_native_configuration_unchanged(self):
        self.assertEqual(keyboard.xorg_keyboard(None), "")
        self.assertEqual(keyboard.weston_keyboard("existing", None), "existing")
        keyboard.validate_keyboard(None)


@unittest.skipUnless(sys.platform == "linux", "requires Linux XKB library")
class NativeKeyboardTests(unittest.TestCase):
    def test_synthetic_keymap_preserves_logical_keys_and_required_modifiers(self):
        xkb = ctypes.CDLL("libxkbcommon.so.0")
        xkb.xkb_context_new.argtypes = [ctypes.c_int]
        xkb.xkb_context_new.restype = ctypes.c_void_p
        xkb.xkb_context_unref.argtypes = [ctypes.c_void_p]
        xkb.xkb_keymap_new_from_string.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_int,
        ]
        xkb.xkb_keymap_new_from_string.restype = ctypes.c_void_p
        xkb.xkb_keymap_unref.argtypes = [ctypes.c_void_p]
        definition = b"""xkb_keymap {
            xkb_keycodes { minimum=8; maximum=255; <TEST>=38; <LFSH>=50; };
            xkb_types { type "TWO_LEVEL" { modifiers=Shift; map[Shift]=Level2; }; };
            xkb_compatibility {};
            xkb_symbols {
                key <TEST> { type="TWO_LEVEL", [ x, 1 ] };
                key <LFSH> { [ Shift_L ] };
                modifier_map Shift { <LFSH> };
            };
        };"""
        context = xkb.xkb_context_new(0)
        self.assertTrue(context)
        keymap = None
        try:
            keymap = xkb.xkb_keymap_new_from_string(context, definition, 1, 0)
            self.assertTrue(keymap)
            chords = keyboard.key_chords(xkb, keymap)
            self.assertEqual(chords["x"], [30])
            self.assertEqual(chords["1"], [42, 30])
            self.assertNotIn("z", chords)
        finally:
            if keymap:
                xkb.xkb_keymap_unref(keymap)
            xkb.xkb_context_unref(context)


if __name__ == "__main__":
    unittest.main()
