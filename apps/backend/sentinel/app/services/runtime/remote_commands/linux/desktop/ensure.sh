#!/usr/bin/env bash
set -euo pipefail
python3 - "$1" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REQ = json.loads(sys.argv[1])
HOME = Path(REQ["home"])
RUNTIME = Path(REQ["runtime"])
LOGS = Path(REQ["logs"])
GEOMETRY = str(REQ["geometry"])
DEPTH = int(REQ["depth"])
META = RUNTIME / "desktop.json"
WORKSPACE_XDG_RUNTIME_DIR = RUNTIME / "xdg"
TMP_XDG_RUNTIME_ROOT = Path("/tmp/sentinel-runtime")
TMP_XDG_RUNTIME_DIR = TMP_XDG_RUNTIME_ROOT / hashlib.sha1(str(REQ["session_id"]).encode()).hexdigest()[:16]
MAX_WORKSPACE_XDG_RUNTIME_DIR_LENGTH = 64
XDG_RUNTIME_DIR = (
    WORKSPACE_XDG_RUNTIME_DIR
    if len(str(WORKSPACE_XDG_RUNTIME_DIR)) <= MAX_WORKSPACE_XDG_RUNTIME_DIR_LENGTH
    else TMP_XDG_RUNTIME_DIR
)


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=True))


def fail(detail: str, code: int = 3) -> None:
    emit({"ok": False, "detail": detail})
    sys.exit(code)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def desktop_command() -> str | None:
    if command_exists("startplasma-x11"):
        return "startplasma-x11"
    if command_exists("startxfce4"):
        return "startxfce4"
    return None


def env() -> dict[str, str]:
    current = os.environ.copy()
    current.update(
        {
            "HOME": str(HOME),
            "USER": current.get("USER") or current.get("LOGNAME") or "sentinel",
            "LOGNAME": current.get("LOGNAME") or current.get("USER") or "sentinel",
            "XDG_RUNTIME_DIR": str(XDG_RUNTIME_DIR),
        }
    )
    return current


def display_alive(display: int) -> bool:
    check = subprocess.run(
        ["bash", "-lc", f"DISPLAY=:{display} xdpyinfo >/dev/null 2>&1"],
        env=env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    return check.returncode == 0


def read_existing() -> dict | None:
    if not META.exists():
        return None
    try:
        data = json.loads(META.read_text())
    except Exception:
        return None
    if isinstance(data, dict) and isinstance(data.get("display"), int):
        return data
    return None


def write_xstartup(command: str) -> None:
    for directory in (HOME / ".vnc", HOME / ".config" / "tigervnc"):
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        xstartup = directory / "xstartup"
        xstartup.write_text(
            "#!/bin/sh\n"
            "unset SESSION_MANAGER\n"
            "unset DBUS_SESSION_BUS_ADDRESS\n"
            f"export XDG_RUNTIME_DIR={shlex.quote(str(XDG_RUNTIME_DIR))}\n"
            "exec dbus-run-session " + command + "\n"
        )
        os.chmod(xstartup, 0o755)


def is_managed_xdg_runtime_dir(path: Path) -> bool:
    path = path.resolve(strict=False)
    workspace_path = WORKSPACE_XDG_RUNTIME_DIR.resolve(strict=False)
    tmp_root = TMP_XDG_RUNTIME_ROOT.resolve(strict=False)
    return path == workspace_path or path.is_relative_to(tmp_root)


def remove_xdg_runtime_dir(path: Path | str | None) -> None:
    if not path:
        return
    candidate = Path(path)
    if not is_managed_xdg_runtime_dir(candidate):
        return
    if candidate.exists():
        if candidate.is_symlink() or candidate.is_file():
            candidate.unlink()
        else:
            shutil.rmtree(candidate)


def prepare_xdg_runtime_dir() -> None:
    if XDG_RUNTIME_DIR == TMP_XDG_RUNTIME_DIR:
        TMP_XDG_RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        os.chmod(TMP_XDG_RUNTIME_ROOT, 0o700)
    remove_xdg_runtime_dir(XDG_RUNTIME_DIR)
    XDG_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(XDG_RUNTIME_DIR, 0o700)


def start_display(command: str, display: int) -> tuple[bool, str]:
    subprocess.run(["vncserver", "-kill", f":{display}"], env=env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prepare_xdg_runtime_dir()
    result = subprocess.run(
        [
            "vncserver",
            f":{display}",
            "-localhost",
            "yes",
            "-geometry",
            GEOMETRY,
            "-depth",
            str(DEPTH),
            "-SecurityTypes",
            "None",
        ],
        env=env(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "").strip()
    for _ in range(20):
        if display_alive(display):
            return True, ""
        time.sleep(0.25)
    return False, "VNC server started but X display did not become reachable."


def main() -> None:
    if not command_exists("python3"):
        fail("python3 is required.")
    if not command_exists("vncserver"):
        fail("vncserver is required.")
    if not command_exists("xdpyinfo"):
        fail("xdpyinfo is required.")
    command = desktop_command()
    if command is None:
        fail("startxfce4 or startplasma-x11 is required.")

    HOME.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    if XDG_RUNTIME_DIR == TMP_XDG_RUNTIME_DIR:
        TMP_XDG_RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        os.chmod(TMP_XDG_RUNTIME_ROOT, 0o700)
    os.chmod(HOME, 0o700)
    os.chmod(RUNTIME, 0o700)
    XDG_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(XDG_RUNTIME_DIR, 0o700)
    write_xstartup(command)

    existing = read_existing()
    if (
        existing
        and existing.get("geometry") == GEOMETRY
        and existing.get("xdg_runtime_dir") == str(XDG_RUNTIME_DIR)
        and display_alive(int(existing["display"]))
    ):
        emit({"ok": True, **existing})
        return
    if existing and isinstance(existing.get("display"), int):
        subprocess.run(["vncserver", "-kill", f":{int(existing['display'])}"], env=env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        remove_xdg_runtime_dir(existing.get("xdg_runtime_dir"))
        META.unlink(missing_ok=True)

    start = 20 + (int(hashlib.sha1(str(REQ["session_id"]).encode()).hexdigest(), 16) % 80)
    last_error = ""
    for offset in range(80):
        display = 20 + ((start - 20 + offset) % 80)
        if display_alive(display):
            continue
        ok, last_error = start_display(command, display)
        if not ok:
            continue
        payload = {
            "display": display,
            "port": 5900 + display,
            "geometry": GEOMETRY,
            "desktop_command": command,
            "xdg_runtime_dir": str(XDG_RUNTIME_DIR),
            "xdg_runtime_ephemeral": XDG_RUNTIME_DIR == TMP_XDG_RUNTIME_DIR,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        META.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        os.chmod(META, 0o600)
        emit({"ok": True, **payload})
        return

    fail(last_error or "No available VNC display could be started.")


main()
PY
