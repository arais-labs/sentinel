#!/usr/bin/env bash
set -euo pipefail
python3 - "$1" <<'PY'
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

request = json.loads(sys.argv[1])
metadata_path = Path(request["runtime"]) / "browser.json"

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

try:
    metadata = json.loads(metadata_path.read_text())
except Exception:
    metadata = {}
pid = metadata.get("pid")
if isinstance(pid, int):
    kill_pid(pid)
try:
    metadata_path.unlink()
except FileNotFoundError:
    pass
print(json.dumps({"ok": True}, separators=(",", ":")))
PY
