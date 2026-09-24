"""Select advertised display modes through each desktop's native API.

Call after the real user session publishes its environment. No configuration
files, invented modelines, connector-name assumptions, or background polling.
The returned refresh rate is the compositor's selected mode, not measured FPS.
"""

import json
import math
import re
import subprocess
from dataclasses import dataclass
from desktop_modes import DESKTOP_REFRESH_HZ


@dataclass(frozen=True)
class Mode:
    name: str
    width: int
    height: int
    refresh_hz: float
    current: bool = False


@dataclass
class Output:
    name: str
    modes: list


def single_output(outputs):
    if len(outputs) != 1:
        raise RuntimeError("Desktop output configuration requires exactly one enabled display")
    output = outputs[0]
    if sum(mode.current for mode in output.modes) != 1:
        raise RuntimeError("Desktop did not report one current display mode")
    for mode in output.modes:
        if (
            mode.width <= 0
            or mode.height <= 0
            or not math.isfinite(mode.refresh_hz)
            or mode.refresh_hz <= 0
        ):
            raise RuntimeError("Desktop reported an invalid display mode")
    return output


def parse_xrandr(text):
    outputs, active = [], None
    for line in text.splitlines():
        if line and not line[0].isspace():
            active = None
            fields = line.split()
            if (
                len(fields) > 1
                and fields[1] == "connected"
                and re.search(r"\b\d+x\d+\+\d+\+\d+", line)
            ):
                active = Output(fields[0], [])
                outputs.append(active)
        elif active:
            fields = line.split()
            size = re.match(r"^(\d+)x(\d+)(?:_|$)", fields[0]) if fields else None
            if not size:
                continue
            for rate in fields[1:]:
                if re.fullmatch(r"[0-9.]+[+*]*", rate):
                    active.modes.append(
                        Mode(
                            fields[0],
                            int(size[1]),
                            int(size[2]),
                            float(rate.rstrip("+*")),
                            "*" in rate,
                        )
                    )
    return single_output(outputs)


def parse_wlr(text):
    outputs, active, enabled = [], None, False
    for line in [*text.splitlines(), "END"]:
        if line and not line[0].isspace():
            if active and enabled:
                outputs.append(active)
            active, enabled = Output(line.split()[0], []), False
        elif active:
            if line.strip() == "Enabled: yes":
                enabled = True
            mode = re.match(r"\s+(\d+)x(\d+) px, ([0-9.]+) Hz(.*)$", line)
            if mode:
                width, height, rate = int(mode[1]), int(mode[2]), float(mode[3])
                active.modes.append(
                    Mode(
                        f"{width}x{height}@{rate:g}Hz",
                        width,
                        height,
                        rate,
                        "current" in mode[4],
                    )
                )
    return single_output(outputs)


def parse_plasma(text):
    outputs = []
    for value in json.loads(text)["outputs"]:
        # KScreen omits enabled when the output cannot be disabled.
        if value["connected"] and value.get("enabled", True):
            modes = [
                Mode(
                    str(mode["id"]),
                    mode["size"]["width"],
                    mode["size"]["height"],
                    mode["refreshRate"],
                    str(mode["id"]) == str(value["currentModeId"]),
                )
                for mode in value["modes"]
            ]
            outputs.append(Output(str(value["id"]), modes))
    try:
        return single_output(outputs)
    except RuntimeError as error:
        # Preserve the actual compositor snapshot: a later diagnostic query may
        # already see a different topology during session startup.
        raise RuntimeError(f"{error}; KScreen snapshot: {text}") from error


def parse_gnome(state):
    _, monitors, logical, _ = state
    enabled = [monitor[0] for group in logical for monitor in group[5]]
    outputs = []
    for identity, modes, _ in monitors:
        if identity[0] in enabled:
            outputs.append(
                Output(
                    identity[0],
                    [
                        Mode(
                            mode[0],
                            mode[1],
                            mode[2],
                            mode[3],
                            mode[6].get("is-current", False),
                        )
                        for mode in modes
                    ],
                )
            )
    return single_output(outputs)


def select_mode(output, width, height):
    candidates = [mode for mode in output.modes if (mode.width, mode.height) == (width, height)]
    # Prefer the requested 120 Hz mode. Otherwise use the best advertised rate
    # not exceeding it; if only faster rates exist select the nearest one.
    at_target = [mode for mode in candidates if abs(mode.refresh_hz - DESKTOP_REFRESH_HZ) < 0.1]
    below = [mode for mode in candidates if mode.refresh_hz <= DESKTOP_REFRESH_HZ]
    if at_target:
        return min(
            at_target,
            key=lambda mode: (abs(mode.refresh_hz - DESKTOP_REFRESH_HZ), not mode.current),
        )
    if below:
        return max(below, key=lambda mode: (mode.refresh_hz, mode.current))
    return min(candidates, key=lambda mode: mode.refresh_hz) if candidates else None


def _command(arguments, environment, credentials):
    try:
        return subprocess.run(
            arguments,
            env={**environment, "LC_ALL": "C"},
            text=True,
            capture_output=True,
            check=True,
            timeout=8,
            **credentials,
        ).stdout
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"Display command failed: {error.stderr[-2048:]}") from error


class NativeOutput:
    def __init__(self, desktop, environment, credentials):
        self.desktop, self.environment, self.credentials = (
            desktop,
            environment,
            credentials,
        )
        self.state = None

    def run(self, arguments):
        return _command(arguments, self.environment, self.credentials)

    def gnome_call(self, method, *arguments):
        return self.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.gnome.Mutter.DisplayConfig",
                "--object-path",
                "/org/gnome/Mutter/DisplayConfig",
                "--method",
                "org.gnome.Mutter.DisplayConfig." + method,
                *arguments,
            ]
        )

    def read(self):
        if self.desktop == "xfce":
            return parse_xrandr(self.run(["xrandr", "--query"]))
        if self.desktop == "lxqt":
            return parse_wlr(self.run(["wlr-randr"]))
        if self.desktop == "plasma":
            return parse_plasma(self.run(["kscreen-doctor", "--json"]))
        if self.desktop == "gnome":
            from gi.repository import GLib

            self.state = GLib.Variant.parse(
                None, self.gnome_call("GetCurrentState"), None, None
            ).unpack()
            return parse_gnome(self.state)
        raise ValueError("Unknown native desktop output adapter")

    def apply(self, output, mode):
        if self.desktop == "xfce":
            self.run(
                [
                    "xrandr",
                    "--output",
                    output.name,
                    "--mode",
                    mode.name,
                    "--rate",
                    str(mode.refresh_hz),
                ]
            )
        elif self.desktop == "lxqt":
            self.run(["wlr-randr", "--output", output.name, "--mode", mode.name])
        elif self.desktop == "plasma":
            # KScreen's argument grammar uses periods to separate output/id/mode.
            if not output.name.isdecimal() or not mode.name.isdecimal():
                raise RuntimeError("KScreen reported an invalid output or mode ID")
            self.run(["kscreen-doctor", f"output.{output.name}.mode.{mode.name}"])
        elif self.desktop == "gnome":
            from gi.repository import GLib

            serial, _, logical, properties = self.state
            if len(logical) != 1 or len(logical[0][5]) != 1:
                raise RuntimeError("Cloned desktop outputs are not supported")
            group = logical[0]
            configuration = [
                (
                    group[0],
                    group[1],
                    group[2],
                    group[3],
                    group[4],
                    [(output.name, mode.name, {})],
                )
            ]
            options = {}
            if "layout-mode" in properties:
                options["layout-mode"] = GLib.Variant("u", properties["layout-mode"])
            # Method 1 is temporary: never replace the user's monitors.xml.
            self.gnome_call(
                "ApplyMonitorsConfig",
                str(serial),
                "1",
                GLib.Variant("a(iiduba(ssa{sv}))", configuration).print_(False),
                GLib.Variant("a{sv}", options).print_(False),
            )


def configure(desktop, geometry, environment, credentials=None):
    """Return actual selected mode and whether requested geometry/120 Hz is met.

    ``credentials`` is the session's subprocess user/group/extra_groups mapping
    when called by the privileged supervisor; omit inside the regular session.
    Missing advertised geometry leaves the current mode untouched and is reported.
    """
    if not isinstance(geometry, str) or not re.fullmatch(r"[1-9]\d{2,3}x[1-9]\d{2,3}", geometry):
        raise ValueError("Invalid desktop output geometry")
    width, height = map(int, geometry.split("x"))
    adapter = NativeOutput(desktop, environment, credentials or {})
    output = adapter.read()
    selected = select_mode(output, width, height)
    if selected and not selected.current:
        adapter.apply(output, selected)
        output = adapter.read()
    actual = next(mode for mode in output.modes if mode.current)
    geometry_met = (actual.width, actual.height) == (width, height)
    refresh_met = abs(actual.refresh_hz - DESKTOP_REFRESH_HZ) < 0.1
    return {
        "output": output.name,
        "width": actual.width,
        "height": actual.height,
        "refresh_hz": actual.refresh_hz,
        "requested_geometry": geometry,
        "requested_refresh_hz": DESKTOP_REFRESH_HZ,
        "geometry_met": geometry_met,
        "refresh_met": refresh_met,
        "target_met": geometry_met and refresh_met,
    }
