from __future__ import annotations

import json
import os
import select
import signal
import sys
from pathlib import Path

request = json.loads(sys.argv[1])
metadata_path = Path(request["runtime"]) / "browser.json"


def kill_pid(pid):
    if pid <= 0:
        return
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        process = Path(f"/proc/{pid}")
        try:
            fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
            identity = {
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "start_ticks": fields[19],
                "uid": process.stat().st_uid,
            }
        except FileNotFoundError:
            return
        # Chromium rewrites argv; use birth identity and a stable kernel handle.
        # A queued cleanup after reboot or PID reuse must never signal a stranger.
        if identity != metadata.get("process_identity"):
            raise ValueError("Browser process identity does not match; refusing to signal it")
        poller = select.poll()
        poller.register(descriptor, select.POLLIN)
        try:
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            if not poller.poll(5000):
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                if not poller.poll(5000):
                    raise TimeoutError("Browser did not exit after SIGKILL")
        except ProcessLookupError:
            pass
    finally:
        os.close(descriptor)


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
