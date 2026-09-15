"""Workspace-only X11 controller. Sent over the existing container transport.

XGetImage reads the root framebuffer; XTEST injects pointer/buttons/key chords.
xdotool handles Unicode typing through X keyboard mappings (never a shell).
"""

import base64
import fcntl
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from uuid import UUID

from Xlib import X, XK, display
from Xlib.ext import xtest
from PIL import Image


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
    return value


def validate(request, width, height):
    if not isinstance(request, dict):
        raise ValueError("Request must be an object")
    actions = request.get("actions", [])
    if not isinstance(actions, list) or len(actions) > 16:
        raise ValueError("Use at most 16 actions per batch")
    if actions and request.get("viewport") != {"width": width, "height": height}:
        raise ValueError("Desktop dimensions changed or viewport missing; take a fresh screenshot")
    allowed = {"move", "click", "double_click", "drag", "scroll", "keypress", "type", "wait"}
    for action in actions:
        if not isinstance(action, dict) or action.get("type") not in allowed:
            raise ValueError("Unsupported computer action")
        kind = action["type"]
        if kind in {"move", "click", "double_click", "scroll"}:
            integer(action.get("x"), "x", 0, width - 1)
            integer(action.get("y"), "y", 0, height - 1)
        if kind in {"click", "double_click", "drag"} and action.get("button", "left") not in {
            "left",
            "middle",
            "right",
        }:
            raise ValueError("Invalid mouse button")
        if kind == "drag":
            path = action.get("path")
            if not isinstance(path, list) or not 2 <= len(path) <= 100:
                raise ValueError("Drag path requires 2–100 points")
            for point in path:
                if not isinstance(point, dict):
                    raise ValueError("Drag point must be an object")
                integer(point.get("x"), "x", 0, width - 1)
                integer(point.get("y"), "y", 0, height - 1)
        if kind == "scroll":
            integer(action.get("ticks"), "ticks", 1, 20)
            if action.get("direction") not in {"up", "down", "left", "right"}:
                raise ValueError("Invalid scroll direction")
        if kind == "keypress":
            keys = action.get("keys")
            if not isinstance(keys, list) or not 1 <= len(keys) <= 5:
                raise ValueError("keypress requires 1–5 key names")
            if any(
                not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,24}", key)
                for key in keys
            ):
                raise ValueError("Use X11 key names, e.g. Control_L, Return, Left")
        if kind == "type":
            text = action.get("text")
            if not isinstance(text, str) or len(text.encode("utf-8")) > 4000 or "\x00" in text:
                raise ValueError("Text must be at most 4000 UTF-8 bytes without NUL")
        if kind == "wait":
            integer(action.get("milliseconds"), "milliseconds", 0, 2000)
    return actions


class Desktop:
    def __init__(self):
        self.X, self.XK, self.xtest, self.Image = X, XK, xtest, Image
        self.display = display.Display(":1")
        if not self.display.has_extension("XTEST"):
            raise RuntimeError("Workspace display does not support XTEST")
        self.root = self.display.screen().root
        self.held = []

    def geometry(self):
        geometry = self.root.get_geometry()
        return geometry.width, geometry.height

    def move(self, point):
        self.xtest.fake_input(self.display, self.X.MotionNotify, x=point["x"], y=point["y"])
        self.display.sync()

    def press(self, kind, code):
        self.held.append((kind, code))
        self.xtest.fake_input(self.display, kind, code)
        self.display.sync()

    def release(self):
        while self.held:
            kind, code = self.held.pop()
            self.xtest.fake_input(
                self.display,
                self.X.KeyRelease if kind == self.X.KeyPress else self.X.ButtonRelease,
                code,
            )
        self.display.sync()

    def keycodes(self, keys):
        codes = [self.display.keysym_to_keycode(self.XK.string_to_keysym(key)) for key in keys]
        if not all(codes):
            raise ValueError("Unknown or unmapped X11 key name")
        return codes

    def execute(self, action):
        kind = action["type"]
        button = {"left": 1, "middle": 2, "right": 3}.get(action.get("button", "left"))
        try:
            if kind in {"move", "click", "double_click", "scroll"}:
                self.move(action)
            if kind in {"click", "double_click"}:
                for _ in range(2 if kind == "double_click" else 1):
                    self.press(self.X.ButtonPress, button)
                    self.release()
                    time.sleep(0.06)
            elif kind == "drag":
                self.move(action["path"][0])
                self.press(self.X.ButtonPress, button)
                for point in action["path"][1:]:
                    self.move(point)
                    time.sleep(0.01)
            elif kind == "scroll":
                code = {"up": 4, "down": 5, "left": 6, "right": 7}[action["direction"]]
                for _ in range(action["ticks"]):
                    self.press(self.X.ButtonPress, code)
                    self.release()
            elif kind == "keypress":
                for code in self.keycodes(action["keys"]):
                    self.press(self.X.KeyPress, code)
            elif kind == "type":
                # A pipe avoids command-line text exposure and shell interpolation.
                subprocess.run(
                    ["xdotool", "type", "--clearmodifiers", "--delay", "1", "--file", "-"],
                    input=action["text"],
                    text=True,
                    check=True,
                    timeout=8,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env={**os.environ, "DISPLAY": ":1", "LC_ALL": "C.UTF-8"},
                )
            elif kind == "wait":
                time.sleep(action["milliseconds"] / 1000)
        finally:
            self.release()

    def screenshot(self):
        width, height = self.geometry()
        if width * height > 16_000_000:
            raise RuntimeError("Desktop exceeds screenshot pixel limit")
        raw = self.root.get_image(0, 0, width, height, self.X.ZPixmap, 0xFFFFFFFF)
        info = self.display.display.info
        fmt = next(f for f in info.pixmap_formats if f.depth == raw.depth)
        visual = next(
            v
            for depth in self.display.screen().allowed_depths
            for v in depth.visuals
            if v.visual_id == self.display.screen().root_visual
        )
        if (visual.red_mask, visual.green_mask, visual.blue_mask) != (0xFF0000, 0xFF00, 0xFF):
            raise RuntimeError("Unsupported desktop pixel masks")
        if fmt.bits_per_pixel not in (24, 32):
            raise RuntimeError("Unsupported desktop pixel format")
        little = info.image_byte_order == self.X.LSBFirst
        mode = (
            ("BGRX" if little else "XRGB")
            if fmt.bits_per_pixel == 32
            else ("BGR" if little else "RGB")
        )
        stride = ((width * fmt.bits_per_pixel + fmt.scanline_pad - 1) // fmt.scanline_pad) * (
            fmt.scanline_pad // 8
        )
        image = self.Image.frombytes("RGB", (width, height), raw.data, "raw", mode, stride, 1)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        mime = "image/png"
        if buffer.tell() > 1_400_000:
            # Preserve exact coordinates; compress instead of silently resizing.
            for quality in (85, 65, 45):
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=quality)
                mime = "image/jpeg"
                if buffer.tell() <= 1_400_000:
                    break
        if buffer.tell() > 1_400_000:
            raise RuntimeError("Screenshot exceeds attachment limit; reduce desktop resolution")
        pointer = self.root.query_pointer()
        return {
            "viewport": {"width": width, "height": height},
            "cursor": {"x": pointer.root_x, "y": pointer.root_y},
            "screenshot": "data:"
            + mime
            + ";base64,"
            + base64.b64encode(buffer.getvalue()).decode("ascii"),
        }


def main(request):
    if sys.platform != "linux":
        raise RuntimeError("Computer control only runs inside the Linux workspace")
    if not isinstance(request, dict):
        raise ValueError("Request must be an object")
    request_id = str(UUID(request["request_id"])) if request.get("request_id") else None
    root = Path("/run/sentinel-desktop")
    cancel = root / ("cancel-" + request_id) if request_id else None
    if cancel and cancel.exists():
        cancel.unlink(missing_ok=True)
        raise RuntimeError("Computer action batch cancelled before starting")
    # A kernel lock serializes sessions/processes sharing this workspace display.
    with Path("/run/sentinel-desktop/computer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Workspace desktop is busy; observe again before retrying") from None
        active = root / "computer-active.json"
        active.write_text(json.dumps({"request_id": request_id, "pid": os.getpid()}))
        desktop = None
        completed = 0
        try:
            if cancel and cancel.exists():
                raise RuntimeError("Computer action batch cancelled before starting")
            desktop = Desktop()
            dimensions = desktop.geometry()
            actions = validate(request, *dimensions)
            for action in actions:
                if action["type"] == "keypress":
                    desktop.keycodes(action["keys"])
            for action in actions:
                if desktop.geometry() != dimensions:
                    raise RuntimeError("Desktop resized during batch; take a fresh screenshot")
                desktop.execute(action)
                completed += 1
            time.sleep(0.15)
            return {"ok": True, "completed_actions": completed, **desktop.screenshot()}
        except Exception as exc:
            result = {"ok": False, "completed_actions": completed, "error": str(exc)}
            try:
                result.update(desktop.screenshot())
            except Exception:
                pass
            return result
        finally:
            if desktop is not None:
                desktop.release()
                desktop.display.close()
            active.unlink(missing_ok=True)
            if cancel:
                cancel.unlink(missing_ok=True)


if __name__ == "__main__":

    def interrupted(*_):
        raise TimeoutError("Computer action batch interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGALRM, interrupted)
    signal.alarm(25)
    try:
        print(json.dumps(main(json.loads(sys.argv[1]))))
    except Exception as exc:
        print(json.dumps({"ok": False, "completed_actions": 0, "error": str(exc)}))
        sys.exit(1)
