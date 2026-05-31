#!/usr/bin/env bash
set -euo pipefail
python3 - "$1" <<'PY'
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

request = json.loads(sys.argv[1])
browser_dir = Path(request["browser"])
profile_dir = browser_dir / "chromium"
runtime_dir = Path(request["runtime"])
logs_dir = Path(request["logs"])
metadata_path = runtime_dir / "browser.json"
desktop_metadata_path = runtime_dir / "desktop.json"

def emit(payload):
    print(json.dumps(payload, separators=(",", ":")))

def fail(detail):
    emit({"ok": False, "detail": detail})
    sys.exit(0)

def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

def kill_pid(pid):
    if not pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + 5
    while time.time() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return

def cdp_ready(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False

def read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}

browser_dir.mkdir(parents=True, exist_ok=True)
profile_dir.mkdir(parents=True, exist_ok=True)
runtime_dir.mkdir(parents=True, exist_ok=True)
logs_dir.mkdir(parents=True, exist_ok=True)

existing = read_json(metadata_path)
pid = existing.get("pid")
port = existing.get("port")
if isinstance(pid, int) and isinstance(port, int) and pid_alive(pid) and cdp_ready(port):
    emit({"ok": True, "pid": pid, "port": port, "profile_dir": str(profile_dir), "reused": True})
    sys.exit(0)
if isinstance(pid, int):
    kill_pid(pid)

for stale_name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
    stale = profile_dir / stale_name
    try:
        if stale.exists() or stale.is_symlink():
            stale.unlink()
    except Exception:
        pass

binary = None
for candidate in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
    found = shutil.which(candidate)
    if found:
        binary = found
        break
if binary is None:
    fail("Required executable 'chromium' is not available in the runtime PATH.")

desktop_metadata = read_json(desktop_metadata_path)
display = str(request.get("display") or desktop_metadata.get("display") or "")
if not display:
    fail("Desktop display metadata is missing; start the session desktop first.")

xdg_runtime_dir = str(desktop_metadata.get("xdg_runtime_dir") or (Path(request["session_root"]) / "tmp" / "browser-xdg"))
Path(xdg_runtime_dir).mkdir(parents=True, exist_ok=True)
try:
    os.chmod(xdg_runtime_dir, 0o700)
except Exception:
    pass

def available_port():
    for candidate in range(9300, 9900):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError("No available Chromium CDP port in 9300-9899.")

port = available_port()
geometry = str(request.get("geometry") or "1600x1000")
width, _, height = geometry.partition("x")
window_size = f"{width}x{height}" if width.isdigit() and height.isdigit() else "1600x1000"
log_file = logs_dir / "chromium.log"
env = os.environ.copy()
env.update(
    {
        "DISPLAY": display,
        "HOME": str(request["home"]),
        "XDG_RUNTIME_DIR": xdg_runtime_dir,
    }
)
Path(request["home"]).mkdir(parents=True, exist_ok=True)

command = [
    binary,
    f"--remote-debugging-port={port}",
    "--remote-debugging-address=127.0.0.1",
    f"--user-data-dir={profile_dir}",
    "--profile-directory=Default",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-dev-shm-usage",
    "--password-store=basic",
    f"--window-size={window_size}",
    "--start-maximized",
    "about:blank",
]
with log_file.open("ab", buffering=0) as log:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )

deadline = time.time() + 20
while time.time() < deadline:
    if process.poll() is not None:
        fail(f"Chromium exited early with status {process.returncode}; see {log_file}.")
    if cdp_ready(port):
        metadata = {
            "schema_version": 1,
            "pid": process.pid,
            "port": port,
            "display": display,
            "profile_dir": str(profile_dir),
            "xdg_runtime_dir": xdg_runtime_dir,
            "log_file": str(log_file),
            "started_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        tmp = metadata_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(metadata, indent=2, sort_keys=True))
        tmp.replace(metadata_path)
        emit({"ok": True, **metadata, "reused": False})
        sys.exit(0)
    time.sleep(0.25)

kill_pid(process.pid)
fail(f"Chromium did not expose CDP on 127.0.0.1:{port}; see {log_file}.")
PY
