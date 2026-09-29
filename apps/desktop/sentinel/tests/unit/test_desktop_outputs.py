import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / "native/graphics/guest/desktop_outputs.py"
spec = importlib.util.spec_from_file_location("desktop_outputs", source)
outputs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = outputs
with patch.object(sys, "path", [str(source.parent), *sys.path]):
    spec.loader.exec_module(outputs)


def xrandr(current="60.00", connector="Virtual-7"):
    return f"""Screen 0: minimum 320 x 200, current 1920 x 1200, maximum 16384 x 16384
{connector} connected primary 1920x1200+0+0 (normal left inverted right x axis y axis)
   1920x1200     60.00{"*" if current == "60.00" else ""}  120.00{"*" if current == "120.00" else ""}
   1280x720      60.00
HDMI-1 disconnected (normal left inverted right x axis y axis)
"""


def wlr(current=60):
    return f"""Virtual-9 "Sentinel virtual display"
  Enabled: yes
  Modes:
    1920x1200 px, 60.000000 Hz ({"current" if current == 60 else "preferred"})
    1920x1200 px, 120.000000 Hz ({"current" if current == 120 else ""})
  Position: 0,0
  Transform: normal
  Scale: 1.000000
Disabled-1 "Unused display"
  Enabled: no
  Modes:
    800x600 px, 60.000000 Hz (preferred)
"""


def plasma(current="17"):
    return json.dumps(
        {
            "outputs": [
                {
                    "id": 7,
                    "name": "Virtual-9",
                    "connected": True,
                    "enabled": True,
                    "currentModeId": current,
                    "modes": [
                        {
                            "id": "17",
                            "size": {"width": 1920, "height": 1200},
                            "refreshRate": 60.0,
                        },
                        {
                            "id": "18",
                            "size": {"width": 1920, "height": 1200},
                            "refreshRate": 120.0,
                        },
                    ],
                }
            ]
        }
    )


def gnome(current="slow"):
    identity = ("Virtual-9", "Sentinel", "Display", "1")
    return (
        13,
        [
            (
                identity,
                [
                    (
                        "slow",
                        1920,
                        1200,
                        60.0,
                        1.0,
                        [1.0, 2.0],
                        {"is-current": current == "slow"},
                    ),
                    (
                        "fast",
                        1920,
                        1200,
                        120.0,
                        1.0,
                        [1.0, 2.0],
                        {"is-current": current == "fast"},
                    ),
                ],
                {},
            )
        ],
        [(0, 0, 2.0, 0, True, [identity], {})],
        {"layout-mode": 1},
    )


class OutputTests(unittest.TestCase):
    def test_invalid_plasma_topology_preserves_failing_snapshot(self):
        for count in (0, 2):
            snapshot = json.dumps({"outputs": json.loads(plasma())["outputs"] * count})
            with self.subTest(count=count), self.assertRaises(RuntimeError) as failure:
                outputs.parse_plasma(snapshot)
            self.assertIn(snapshot, str(failure.exception))

    def test_native_parsers_preserve_real_connector_and_advertised_modes(self):
        for parser, value, name in [
            (outputs.parse_xrandr, xrandr(), "Virtual-7"),
            (outputs.parse_wlr, wlr(), "Virtual-9"),
            (outputs.parse_plasma, plasma(), "7"),
            (outputs.parse_gnome, gnome(), "Virtual-9"),
        ]:
            with self.subTest(parser=parser.__name__):
                display = parser(value)
                self.assertEqual(display.name, name)
                self.assertEqual(
                    next(mode.refresh_hz for mode in display.modes if mode.current), 60
                )
                self.assertEqual(outputs.select_mode(display, 1920, 1200).refresh_hz, 120)

    def test_all_command_adapters_apply_then_verify_as_session_user(self):
        environment = {
            "DISPLAY": ":9",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        }
        credentials = {"user": 1000, "group": 1000, "extra_groups": [44]}
        for desktop, before, after, command in [
            (
                "xfce",
                xrandr(),
                xrandr("120.00"),
                [
                    "xrandr",
                    "--output",
                    "Virtual-7",
                    "--mode",
                    "1920x1200",
                    "--rate",
                    "120.0",
                ],
            ),
            (
                "lxqt",
                wlr(),
                wlr(120),
                ["wlr-randr", "--output", "Virtual-9", "--mode", "1920x1200@120Hz"],
            ),
            ("plasma", plasma(), plasma("18"), ["kscreen-doctor", "output.7.mode.18"]),
        ]:
            with (
                self.subTest(desktop=desktop),
                patch.object(outputs, "_command", side_effect=[before, "", after]) as run,
            ):
                result = outputs.configure(desktop, "1920x1200", environment, credentials)
                self.assertTrue(result["target_met"])
                self.assertEqual(result["refresh_hz"], 120)
                self.assertEqual(run.call_args_list[1].args, (command, environment, credentials))

    def test_no_available_geometry_leaves_display_unchanged_reports_actual(self):
        with patch.object(outputs, "_command", return_value=xrandr()) as run:
            result = outputs.configure("xfce", "3840x2160", {})
        self.assertFalse(result["geometry_met"])
        self.assertFalse(result["target_met"])
        self.assertEqual(
            (result["width"], result["height"], result["refresh_hz"]), (1920, 1200, 60)
        )
        self.assertEqual(run.call_count, 1)

    def test_resize_selects_contract_rate_instead_of_initial_or_slow_mode(self):
        contract = json.loads((source.parent.parent / "display/modes.json").read_text())
        for width, height in contract["modes"]:
            with self.subTest(geometry=(width, height)):
                display = outputs.Output(
                    "virtual",
                    [
                        outputs.Mode("initial", 1024, 768, 120, True),
                        outputs.Mode("slow", width, height, 60),
                        outputs.Mode("target", width, height, 119.97),
                    ],
                )
                self.assertEqual(outputs.select_mode(display, width, height).name, "target")

    def test_missing_120_mode_does_not_create_fake_modeline(self):
        with patch.object(
            outputs, "_command", return_value=xrandr().replace("  120.00", "")
        ) as run:
            result = outputs.configure("xfce", "1920x1200", {})
        self.assertTrue(result["geometry_met"])
        self.assertFalse(result["refresh_met"])
        self.assertEqual(result["refresh_hz"], 60)
        self.assertEqual(run.call_count, 1)

    def test_report_does_not_assume_apply_command_changed_mode(self):
        with patch.object(outputs, "_command", side_effect=[wlr(), "", wlr()]):
            result = outputs.configure("lxqt", "1920x1200", {})
        self.assertFalse(result["target_met"])
        self.assertEqual(result["refresh_hz"], 60)

    def test_no_enabled_or_multiple_outputs_fail_before_mutation(self):
        for text in [
            "Screen 0:\nHDMI-1 disconnected\n",
            xrandr() + xrandr(connector="Virtual-8"),
        ]:
            with (
                self.subTest(text=text),
                patch.object(outputs, "_command", return_value=text) as run,
            ):
                with self.assertRaisesRegex(RuntimeError, "exactly one enabled"):
                    outputs.configure("xfce", "1920x1200", {})
                self.assertEqual(run.call_count, 1)

    def test_invalid_mode_refresh_is_rejected(self):
        display = outputs.Output("test", [outputs.Mode("mode", 1920, 1200, float("nan"), True)])
        with self.assertRaisesRegex(RuntimeError, "invalid display mode"):
            outputs.single_output([display])

    def test_current_requested_mode_is_idempotent(self):
        with patch.object(outputs, "_command", return_value=plasma("18")) as run:
            result = outputs.configure("plasma", "1920x1200", {})
        self.assertTrue(result["target_met"])
        self.assertEqual(run.call_count, 1)

    def test_geometry_input_is_not_a_command_fragment(self):
        for value in [None, "0x0", "1920x1200;evil", "1920x1200\n", "-1x1200"]:
            with self.subTest(value=value), patch.object(outputs, "_command") as run:
                with self.assertRaises(ValueError):
                    outputs.configure("xfce", value, {})
                run.assert_not_called()

    def test_gnome_uses_temporary_config_preserves_scale_and_layout(self):
        variants = []

        class Variant:
            def __init__(self, signature, value):
                self.signature, self.value = signature, value
                variants.append(self)

            def print_(self, _):
                return self.signature

        import types

        repository = types.ModuleType("gi.repository")
        repository.GLib = types.SimpleNamespace(Variant=Variant)
        adapter = outputs.NativeOutput("gnome", {}, {})
        adapter.state = gnome()
        display = outputs.parse_gnome(adapter.state)
        with (
            patch.dict(sys.modules, {"gi.repository": repository}),
            patch.object(adapter, "gnome_call") as call,
        ):
            adapter.apply(display, outputs.select_mode(display, 1920, 1200))
        self.assertEqual(
            call.call_args.args,
            ("ApplyMonitorsConfig", "13", "1", "a(iiduba(ssa{sv}))", "a{sv}"),
        )
        self.assertEqual(variants[1].value, [(0, 0, 2.0, 0, True, [("Virtual-9", "fast", {})])])
        self.assertEqual(variants[0].value, 1)

    def test_commands_are_bounded_without_shell_and_forward_native_credentials(self):
        credentials = {"user": 1000, "group": 1000, "extra_groups": [44]}
        with patch.object(outputs.subprocess, "run") as run:
            run.return_value.stdout = "result"
            self.assertEqual(
                outputs._command(["xrandr", "--query"], {"DISPLAY": ":9"}, credentials),
                "result",
            )
        options = run.call_args.kwargs
        self.assertEqual(options["env"], {"DISPLAY": ":9", "LC_ALL": "C"})
        self.assertEqual(options["timeout"], 8)
        self.assertEqual(options["user"], 1000)
        self.assertTrue(options["check"])
        self.assertNotIn("shell", options)

    def test_apply_failure_does_not_report_target_or_retry_mutation(self):
        failure = subprocess.CalledProcessError(1, ["wlr-randr"], stderr="mode rejected")
        with (
            patch.object(outputs, "_command", side_effect=[wlr(), failure]) as run,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            outputs.configure("lxqt", "1920x1200", {})
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
