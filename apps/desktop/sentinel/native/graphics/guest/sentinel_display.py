"""Computer input/capture for the workspace's virtual display, not its compositor.

The worker exposes this private socket inside the guest. A connection owns one
action batch; closing it releases all keys/buttons even if the caller is killed.
Clipboard transfer and logical key chords use the selected desktop session.
"""

import base64
import json
import os
import select
import socket
import struct
import subprocess
import time
from pathlib import Path

KEYS = {
    "Escape": 1,
    "BackSpace": 14,
    "Tab": 15,
    "Return": 28,
    "Control_L": 29,
    "Shift_L": 42,
    "Shift_R": 54,
    "Alt_L": 56,
    "space": 57,
    "Caps_Lock": 58,
    "Num_Lock": 69,
    "Scroll_Lock": 70,
    "KP_Enter": 96,
    "Control_R": 97,
    "Print": 99,
    "Alt_R": 100,
    "Home": 102,
    "Up": 103,
    "Prior": 104,
    "Page_Up": 104,
    "Left": 105,
    "Right": 106,
    "End": 107,
    "Down": 108,
    "Next": 109,
    "Page_Down": 109,
    "Insert": 110,
    "Delete": 111,
    "Pause": 119,
    "Super_L": 125,
    "Super_R": 126,
    "Menu": 127,
    "minus": 12,
    "equal": 13,
    "bracketleft": 26,
    "bracketright": 27,
    "semicolon": 39,
    "apostrophe": 40,
    "grave": 41,
    "backslash": 43,
    "comma": 51,
    "period": 52,
    "slash": 53,
}
for row, first in [
    ("1234567890", 2),
    ("qwertyuiop", 16),
    ("asdfghjkl", 30),
    ("zxcvbnm", 44),
]:
    KEYS.update({letter: first + offset for offset, letter in enumerate(row)})
KEYS.update({f"F{number}": code for number, code in enumerate([*range(59, 69), 87, 88], 1)})


class Clipboard:
    def __init__(self, session_path="/run/sentinel-desktop/session.json"):
        self.session_path = Path(session_path)

    def session(self):
        # The session launcher publishes its display environment once. This is
        # also the contract for an agent-supplied replacement desktop session.
        session = json.loads(self.session_path.read_text())
        return session["clipboard_backend"], {
            **session["environment"],
            "LC_ALL": "C.UTF-8",
        }

    def command(self, backend, environment, *, write):
        if backend == "wayland":
            if not environment.get("WAYLAND_DISPLAY") or not environment.get("XDG_RUNTIME_DIR"):
                raise RuntimeError("Wayland clipboard requires the published desktop display")
            return (
                ["/opt/sentinel/graphics/bin/wl-copy", "--type", "text/plain;charset=utf-8"]
                if write
                else ["/opt/sentinel/graphics/bin/wl-paste", "--no-newline", "--type", "text"]
            )
        if backend == "x11":
            # GNOME uses Mutter's maintained Xwayland selection bridge. KDE is
            # intentionally not routed here: its bridge gates reads on focus.
            if not environment.get("DISPLAY") or not environment.get("XAUTHORITY"):
                raise RuntimeError("X11 clipboard requires the authenticated desktop display")
            return ["/usr/bin/xclip", "-selection", "clipboard", "-in" if write else "-out"]
        raise RuntimeError("Desktop session does not provide clipboard transfer")

    def credentials(self):
        user = json.loads(self.session_path.read_text())["user"]
        if user["uid"] <= 0 or user["gid"] <= 0:
            raise RuntimeError("Desktop clients require a regular user")
        if os.geteuid() == 0:
            return {"user": user["uid"], "group": user["gid"], "extra_groups": user["groups"]}
        if os.geteuid() != user["uid"]:
            raise RuntimeError("Desktop session belongs to another user")
        return {}

    def write(self, text):
        backend, environment = self.session()
        command = self.command(backend, environment, write=True)
        # Text never appears in a shell or process arguments.
        subprocess.run(
            command,
            input=text.encode("utf-8"),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=8,
            **self.credentials(),
        )

    def read(self):
        backend, environment = self.session()
        command = self.command(backend, environment, write=False)
        with subprocess.Popen(
            command,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **self.credentials(),
        ) as process:
            try:
                # A selection owner may never close its pipe. Bound both the
                # pipe read and child exit, not just wait() after a blocking read.
                deadline = time.monotonic() + 5
                data = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                        raise subprocess.TimeoutExpired(command, 5)
                    chunk = os.read(process.stdout.fileno(), min(65536, 262145 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > 262144:
                        raise ValueError("Clipboard text exceeds 256 KiB")
                if process.wait(timeout=max(0, deadline - time.monotonic())) != 0:
                    return ""
                return data.decode("utf-8")
            finally:
                if process.poll() is None:
                    process.kill()


class Desktop:
    def __init__(self, path="/run/sentinel-desktop/display.sock", clipboard=None, timeout=10):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(timeout)
        self.reader = None
        self.clipboard = clipboard or Clipboard()
        try:
            self.socket.connect(path)
            self.reader = self.socket.makefile("rb")
            self.request("acquire")
        except BaseException:
            self.close()
            raise

    def request(self, action, **fields):
        self.socket.sendall(json.dumps({"action": action, **fields}).encode() + b"\n")
        line = self.reader.readline(2_000_001)
        if not line.endswith(b"\n") or len(line) > 2_000_000:
            raise RuntimeError("Virtual display disconnected or response exceeded limit")
        reply = json.loads(line)
        if not isinstance(reply, dict) or not reply.get("ok"):
            raise RuntimeError(
                reply.get("error", "Virtual display is busy or unavailable")
                if isinstance(reply, dict)
                else "Invalid virtual display response"
            )
        return reply

    def geometry(self):
        viewport = self.request("status")["viewport"]
        return viewport["width"], viewport["height"]

    def input(self, device, events):
        events = [*events, (0, 0, 0)]
        body = struct.pack("!HH", device, len(events))
        body += b"".join(struct.pack("!HHi", *event) for event in events)
        self.request("input", data=base64.b64encode(body).decode("ascii"))

    def move(self, point):
        width, height = self.geometry()
        self.input(
            1,
            [
                (3, 0, round(point["x"] * 65535 / max(width - 1, 1))),
                (3, 1, round(point["y"] * 65535 / max(height - 1, 1))),
            ],
        )

    def initialize_pointer(self):
        # The input devices outlive a compositor session. Its cursor can reset
        # while evdev retains the last ABS values and suppresses equal updates.
        # Initialize the absolute device at the center on each new session;
        # two distinct reports guarantee the final position reaches libinput.
        # This runs at session startup, never on reconnect or before a click.
        self.input(
            1,
            [
                (3, 0, 32768),
                (3, 1, 32767),
                (0, 0, 0),
                (3, 0, 32767),
                (3, 1, 32767),
            ],
        )

    def release(self):
        self.request("release")

    def keycodes(self, keys):
        path = self.clipboard.session_path
        mapping = json.loads(path.read_text()).get("keyboard_keys") if path.exists() else None
        try:
            codes = []
            for key in keys:
                key = key.lower() if len(key) == 1 else key
                # Named controls stay explicit; printable keys follow XKB.
                chord = mapping.get(key) if mapping else None
                if chord is None:
                    if mapping is not None and (
                        len(key) == 1
                        or key
                        in {
                            "minus",
                            "equal",
                            "bracketleft",
                            "bracketright",
                            "semicolon",
                            "apostrophe",
                            "grave",
                            "backslash",
                            "comma",
                            "period",
                            "slash",
                        }
                    ):
                        raise KeyError(key)
                    chord = [KEYS[key]]
                codes.extend(code for code in chord if code not in codes)
            return codes
        except KeyError:
            raise ValueError(
                "Unknown key name; use Control_L, Return, Left, a–z, or F1–F12"
            ) from None

    def execute(self, action):
        kind = action["type"]
        button = {"left": 272, "right": 273, "middle": 274}[action.get("button", "left")]
        try:
            if kind in {"move", "click", "double_click", "scroll"}:
                self.move(action)
            if kind == "move":
                return
            if kind in {"click", "double_click"}:
                for _ in range(2 if kind == "double_click" else 1):
                    self.input(1, [(1, button, 1)])
                    self.release()
                    time.sleep(0.06)
            elif kind == "drag":
                self.move(action["path"][0])
                self.input(1, [(1, button, 1)])
                for point in action["path"][1:]:
                    self.move(point)
                    time.sleep(0.01)
            elif kind == "scroll":
                code, direction = {
                    "up": (8, 1),
                    "down": (8, -1),
                    "left": (6, -1),
                    "right": (6, 1),
                }[action["direction"]]
                self.input(0, [(2, code, direction * action["ticks"])])
            elif kind == "keypress":
                self.input(2, [(1, code, 1) for code in self.keycodes(action["keys"])])
            elif kind == "type":
                if action["text"]:
                    protocol, environment = self.clipboard.session()
                    if protocol == "x11":
                        subprocess.run(
                            [
                                "xdotool",
                                "type",
                                "--clearmodifiers",
                                "--delay",
                                "1",
                                "--file",
                                "-",
                            ],
                            input=action["text"].encode(),
                            env=environment,
                            check=True,
                            timeout=8,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            **self.clipboard.credentials(),
                        )
                        return
                    self.clipboard.write(action["text"])
                    # Paste plain text: the terminal must not interpret Ctrl+V
                    # as its "quote next character" command.
                    self.input(
                        2,
                        [(1, code, 1) for code in self.keycodes(["Control_L", "Shift_L", "v"])],
                    )
            elif kind == "wait":
                time.sleep(action["milliseconds"] / 1000)
            else:
                raise ValueError("Unsupported computer action")
        finally:
            self.release()

    def screenshot(self):
        reply = self.request("snapshot")
        return {key: reply[key] for key in ("viewport", "cursor", "screenshot")}

    def close(self):
        # Socket teardown itself releases held input; cleanup must not mask the
        # original partial failure or attempt to replay an interrupted action.
        if self.reader:
            self.reader.close()
        self.socket.close()
