"""Workspace computer batches: validated, cancellable, never automatically replayed."""

import fcntl
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
from uuid import UUID

sys.path.insert(0, "/opt/sentinel/desktop")
from sentinel_display import Desktop


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
                desktop.close()
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
