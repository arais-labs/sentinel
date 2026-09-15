"""One on-demand desktop inside the workspace VM. No host or session setup."""

import fcntl
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from xml.sax.saxutils import escape
from pathlib import Path

request = json.loads(sys.argv[1])
action = request.get("action", "status")
geometry = request.get("geometry") or "1920x1200"
root = Path("/run/sentinel-desktop")
metadata = root / "state.json"
log_path = Path("/var/log/sentinel-desktop.log")
required = ("Xvnc", "xfce4-session", "dbus-run-session", "xdpyinfo")


def identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except (OSError, IndexError):
        return None


def alive(record):
    return bool(
        record and identity(record.get("pid")) == record.get("identity") and record.get("identity")
    )


def read_state():
    try:
        return json.loads(metadata.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    temp = metadata.with_suffix(".next")
    temp.write_text(json.dumps(state))
    temp.replace(metadata)


def stop(state):
    for name in ("session", "server"):
        record = state.get(name)
        if alive(record):
            try:
                os.killpg(record["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 3
    while (
        any(alive(state.get(name)) for name in ("session", "server"))
        and time.monotonic() < deadline
    ):
        time.sleep(0.1)
    for name in ("session", "server"):
        if alive(state.get(name)):
            try:
                os.killpg(state[name]["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
    metadata.unlink(missing_ok=True)


def spawn(command, env, log):
    process = subprocess.Popen(
        command,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return {"pid": process.pid, "identity": identity(process.pid)}


def healthy(state):
    try:
        version = Path("/opt/sentinel/graphics/version").read_text().strip()
    except OSError:
        return False
    return (
        state.get("graphics_version") == version
        and state.get("renderer") == "metal"
        and alive(state.get("server"))
        and alive(state.get("session"))
    )


def seed_appearance(defaults, env):
    # Run before xfconfd starts. Never overwrite a user's saved preferences.
    outputs = subprocess.run(
        ["xrandr", "--query"], env=env, capture_output=True, text=True, timeout=3
    )
    monitor = next(
        (line.split()[0] for line in outputs.stdout.splitlines() if " connected" in line), "VNC-0"
    )
    home = Path(env["HOME"]).resolve()
    for relative, contents in defaults.items():
        target = (home / relative).resolve()
        if not any(
            target.is_relative_to(base)
            for base in (home / ".config", home / ".local/share/backgrounds/sentinel")
        ):
            raise ValueError("Invalid desktop preference path")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("x") as stream:
                stream.write(contents.replace("__MONITOR__", escape(monitor, {'"': "&quot;"})))
        except FileExistsError:
            pass


def main():
    if action not in {"status", "start", "stop"}:
        raise ValueError("Unknown desktop action")
    if not re.fullmatch(
        r"(?:1280x800|1440x900|1680x1050|1920x1200|2560x1600|2880x1800|3840x2400)", geometry
    ):
        raise ValueError("Unsupported desktop resolution")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_state()
        if action == "stop":
            stop(state)
            return {"state": "stopped"}
        missing = [name for name in required if not shutil.which(name)]
        if missing:
            return {
                "state": "not_installed",
                "reason": "Install the Desktop package in workspace settings.",
            }
        if action == "status":
            return {
                "state": "running" if healthy(state) else "stopped",
                "geometry": state.get("geometry", geometry),
                "display": ":1",
                "port": 5901,
            }
        if healthy(state) and state.get("geometry") == geometry:
            return {"state": "running", "geometry": geometry, "display": ":1", "port": 5901}
        stop(state)
        graphics = Path("/opt/sentinel/graphics")
        if not (graphics / "version").is_file():
            raise RuntimeError("Desktop graphics are not installed. Start workspace setup again.")
        if not Path("/run/sentinel-graphics/renderer.sock").is_socket():
            raise RuntimeError("Metal graphics connection is unavailable. Restart the desktop.")
        # Never claim or kill a display belonging to another application.
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 5901)) == 0:
                raise RuntimeError("Desktop port is already in use inside this workspace.")
        env = {
            **os.environ,
            "HOME": "/root",
            "USER": "root",
            "LOGNAME": "root",
            "DISPLAY": ":1",
            "XDG_RUNTIME_DIR": str(root),
            "XDG_SESSION_TYPE": "x11",
        }
        env.pop("SESSION_MANAGER", None)
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        Path("/root").mkdir(exist_ok=True)
        # Chromium runs as root inside the isolated workspace, like agent tools.
        launcher = Path("/root/.local/share/applications/sentinel-browser.desktop")
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text(
            "[Desktop Entry]\nType=Application\nName=Chromium (workspace)\nExec=chromium --no-sandbox %U\nIcon=chromium\nCategories=Network;WebBrowser;\n"
        )
        state = {
            "geometry": geometry,
            "renderer": "metal",
            "graphics_version": (graphics / "version").read_text().strip(),
        }
        try:
            with log_path.open("ab", buffering=0) as log:
                state["server"] = spawn(
                    [
                        "Xvnc",
                        ":1",
                        "-geometry",
                        geometry,
                        "-depth",
                        "24",
                        "-localhost",
                        "yes",
                        "-SecurityTypes",
                        "None",
                        "-rfbport",
                        "5901",
                        "-AlwaysShared",
                        "-nolisten",
                        "tcp",
                    ],
                    env,
                    log,
                )
                save_state(state)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if not alive(state["server"]):
                        raise RuntimeError("Display could not start")
                    result = subprocess.run(
                        ["xdpyinfo", "-display", ":1"],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=1,
                    )
                    if result.returncode == 0:
                        break
                    time.sleep(0.2)
                else:
                    raise RuntimeError("Display did not become ready")
                seed_appearance(request.get("defaults", {}), env)
                # Mesa's software winsys selects the socket transport. Virpipe
                # executes graphics on the host's Metal renderer, not the CPU.
                env.update(
                    {
                        "LIBGL_ALWAYS_SOFTWARE": "1",
                        "GALLIUM_DRIVER": "virpipe",
                        "VTEST_SOCKET_NAME": "/run/sentinel-graphics/renderer.sock",
                        "LD_LIBRARY_PATH": str(graphics / "lib"),
                        "LIBGL_DRIVERS_PATH": str(graphics / "lib/dri"),
                        "CHROMIUM_USER_FLAGS": "--no-sandbox --use-gl=angle --use-angle=gles-egl --disable-vulkan --disable-software-rasterizer --ignore-gpu-blocklist",
                    }
                )
                probe = subprocess.run(
                    ["glxinfo", "-B"], env=env, capture_output=True, text=True, timeout=8
                )
                if probe.returncode or "OpenGL renderer string: virgl" not in probe.stdout:
                    raise RuntimeError("Metal renderer did not become ready")
                state["session"] = spawn(["dbus-run-session", "--", "xfce4-session"], env, log)
                save_state(state)
                time.sleep(0.8)
                if not healthy(state):
                    raise RuntimeError("Desktop session exited during startup")
            save_state(state)
        except Exception as exc:
            stop(state)
            raise RuntimeError(f"{exc}. See {log_path} inside the workspace.") from exc
        return {"state": "running", "geometry": geometry, "display": ":1", "port": 5901}


try:
    print(json.dumps({"ok": True, **main()}))
except Exception as exc:
    print(json.dumps({"ok": False, "reason": str(exc)}))
