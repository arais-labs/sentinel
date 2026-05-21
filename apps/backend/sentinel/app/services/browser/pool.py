from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass
from pathlib import PurePosixPath
from shlex import quote
from typing import Any

from app.services.browser.manager import BrowserManager
from app.services.runtime.desktop import RuntimeDesktopManager
from app.services.runtime.ssh_runtime import (
    get_runtime_desktop_manager,
    get_runtime_terminal_manager,
)
from app.services.runtime.terminal_manager import RuntimeTerminalManager
from app.services.runtime.workspace import workspace_paths


class BrowserPoolError(RuntimeError):
    pass


@dataclass(slots=True)
class _BrowserHandle:
    manager: BrowserManager
    listener: object
    local_port: int
    remote_port: int
    pid: int
    cdp_endpoint: str


def _allocate_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class BrowserPool:
    """Session-scoped visible Chromium instances controlled through SSH/CDP."""

    def __init__(
        self,
        *,
        terminal_manager: RuntimeTerminalManager | None = None,
        desktop_manager: RuntimeDesktopManager | None = None,
        manager_cls: type[BrowserManager] = BrowserManager,
        workspaces_root: str | None = None,
    ) -> None:
        self._terminal_manager = terminal_manager
        self._desktop_manager = desktop_manager
        self._manager_cls = manager_cls
        self._workspaces_root = workspaces_root
        self._handles: dict[str, _BrowserHandle] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, session_id: str) -> BrowserManager:
        sid = str(session_id)
        lock = self._locks.setdefault(sid, asyncio.Lock())
        async with lock:
            handle = self._handles.get(sid)
            if handle is not None:
                try:
                    await handle.manager.ensure_connected()
                    return handle.manager
                except Exception:
                    await self._close_handle(handle)
                    self._handles.pop(sid, None)
                    await self._stop_remote(sid)
            handle = await self._start_remote_with_retry(sid)
            self._handles[sid] = handle
            return handle.manager

    async def reset(self, session_id: str) -> dict[str, Any]:
        sid = str(session_id)
        lock = self._locks.setdefault(sid, asyncio.Lock())
        async with lock:
            handle = self._handles.pop(sid, None)
            if handle is not None:
                await self._close_handle(handle)
            await self._reset_remote(sid)
            handle = await self._start_remote_with_retry(sid)
            self._handles[sid] = handle
            state = await handle.manager.warmup()
            return {
                "reset": True,
                "profile_dir": str(PurePosixPath(workspace_paths(sid, root=self._workspaces_root).browser) / "chromium"),
                "cdp_endpoint": handle.cdp_endpoint,
                **state,
            }

    async def remove(self, session_id: str) -> None:
        sid = str(session_id)
        lock = self._locks.setdefault(sid, asyncio.Lock())
        async with lock:
            handle = self._handles.pop(sid, None)
            if handle is not None:
                await self._close_handle(handle)
            await self._stop_remote(sid)

    async def close_all(self) -> None:
        for sid in list(self._handles):
            await self.remove(sid)

    async def _start_remote_with_retry(self, session_id: str) -> _BrowserHandle:
        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                return await self._start_remote(session_id)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                try:
                    await self._stop_remote(session_id)
                except Exception:
                    pass
                if attempt == 2:
                    break
        raise BrowserPoolError(f"Failed to connect to runtime browser: {last_exc}") from last_exc

    async def _start_remote(self, session_id: str) -> _BrowserHandle:
        terminal_manager = self._terminal_manager or get_runtime_terminal_manager()
        desktop_manager = self._desktop_manager or get_runtime_desktop_manager()
        desktop = await desktop_manager.ensure_session_desktop(session_id)
        await terminal_manager.prepare_workspace(session_id)
        result = await terminal_manager.ssh.run_script(
            _build_browser_start_script(
                session_id,
                root=self._workspaces_root,
                display=desktop.display,
                geometry=desktop.geometry,
            ),
            timeout=45,
        )
        if result.exit_status not in {0, None}:
            detail = (result.stderr or result.stdout or "browser start failed").strip()[:1200]
            raise BrowserPoolError(detail)
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise BrowserPoolError("Browser start response was not valid JSON.") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise BrowserPoolError(str(payload.get("detail") or "Browser start failed."))

        remote_port = int(payload["port"])
        local_port = _allocate_local_port()
        try:
            listener = await terminal_manager.ssh.forward_local_port(
                "127.0.0.1",
                local_port,
                "127.0.0.1",
                remote_port,
            )
        except Exception as exc:  # noqa: BLE001
            await self._stop_remote(session_id)
            raise BrowserPoolError(f"Failed to open browser CDP SSH tunnel: {exc}") from exc

        cdp_endpoint = f"http://127.0.0.1:{local_port}"
        manager = self._manager_cls(
            cdp_endpoint=cdp_endpoint,
            user_data_dir=str(PurePosixPath(workspace_paths(session_id, root=self._workspaces_root).browser) / "chromium"),
        )
        try:
            await manager.ensure_connected()
        except Exception:
            await _close_listener(listener)
            try:
                await manager.close()
            finally:
                raise
        return _BrowserHandle(
            manager=manager,
            listener=listener,
            local_port=local_port,
            remote_port=remote_port,
            pid=int(payload["pid"]),
            cdp_endpoint=cdp_endpoint,
        )

    async def _stop_remote(self, session_id: str) -> None:
        terminal_manager = self._terminal_manager or get_runtime_terminal_manager()
        await terminal_manager.ssh.run_script(
            _build_browser_stop_script(session_id, root=self._workspaces_root),
            timeout=30,
        )

    async def _reset_remote(self, session_id: str) -> None:
        terminal_manager = self._terminal_manager or get_runtime_terminal_manager()
        result = await terminal_manager.ssh.run_script(
            _build_browser_reset_script(session_id, root=self._workspaces_root),
            timeout=45,
        )
        if result.exit_status not in {0, None}:
            detail = (result.stderr or result.stdout or "browser reset failed").strip()[:1200]
            raise BrowserPoolError(detail)

    async def _close_handle(self, handle: _BrowserHandle) -> None:
        try:
            await handle.manager.close()
        finally:
            await _close_listener(handle.listener)


async def _close_listener(listener: object) -> None:
    close = getattr(listener, "close", None)
    if callable(close):
        close()
    wait_closed = getattr(listener, "wait_closed", None)
    if callable(wait_closed):
        maybe_coro = wait_closed()
        if asyncio.iscoroutine(maybe_coro):
            await maybe_coro


def _browser_request(session_id: str, *, root: str | None, display: str | None = None, geometry: str | None = None) -> dict[str, object]:
    paths = workspace_paths(session_id, root=root)
    request: dict[str, object] = {
        "session_id": paths.session_id,
        "session_root": paths.session_root,
        "home": paths.home,
        "runtime": paths.runtime,
        "browser": paths.browser,
        "logs": paths.logs,
    }
    if display is not None:
        request["display"] = display
    if geometry is not None:
        request["geometry"] = geometry
    return request


def _build_browser_start_script(session_id: str, *, root: str | None, display: str, geometry: str) -> str:
    return _REMOTE_BROWSER_START_SCRIPT.replace(
        "__SENTINEL_BROWSER_REQUEST__",
        quote(json.dumps(_browser_request(session_id, root=root, display=display, geometry=geometry), separators=(",", ":"))),
    )


def _build_browser_stop_script(session_id: str, *, root: str | None) -> str:
    return _REMOTE_BROWSER_STOP_SCRIPT.replace(
        "__SENTINEL_BROWSER_REQUEST__",
        quote(json.dumps(_browser_request(session_id, root=root), separators=(",", ":"))),
    )


def _build_browser_reset_script(session_id: str, *, root: str | None) -> str:
    return _REMOTE_BROWSER_RESET_SCRIPT.replace(
        "__SENTINEL_BROWSER_REQUEST__",
        quote(json.dumps(_browser_request(session_id, root=root), separators=(",", ":"))),
    )


_REMOTE_BROWSER_START_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail
python3 - __SENTINEL_BROWSER_REQUEST__ <<'PY'
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
"""


_REMOTE_BROWSER_STOP_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail
python3 - __SENTINEL_BROWSER_REQUEST__ <<'PY'
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
"""


_REMOTE_BROWSER_RESET_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail
python3 - __SENTINEL_BROWSER_REQUEST__ <<'PY'
from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

request = json.loads(sys.argv[1])
browser_dir = Path(request["browser"])
profile_dir = browser_dir / "chromium"
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
if profile_dir.exists() or profile_dir.is_symlink():
    shutil.rmtree(profile_dir)
browser_dir.mkdir(parents=True, exist_ok=True)
print(json.dumps({"ok": True, "profile_dir": str(profile_dir)}, separators=(",", ":")))
PY
"""
