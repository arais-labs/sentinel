from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shlex import quote
from uuid import UUID

from app.services.runtime.ssh_client import SSHClient
from app.services.runtime.terminal_manager import RuntimeTerminalManager
from app.services.runtime.workspace import workspace_paths


class RuntimeDesktopError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeDesktop:
    session_id: str
    display: str
    target_host: str
    target_port: int
    geometry: str
    local_host: str
    local_port: int


@dataclass(slots=True)
class _DesktopHandle:
    desktop: RuntimeDesktop
    listener: object


def _allocate_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class RuntimeDesktopManager:
    def __init__(
        self,
        terminal_manager: RuntimeTerminalManager,
        *,
        workspaces_root: str | None = None,
        geometry: str = "1920x1200",
        depth: int = 24,
    ) -> None:
        self._terminal_manager = terminal_manager
        self._ssh: SSHClient = terminal_manager.ssh
        self._workspaces_root = workspaces_root
        self._geometry = geometry
        self._depth = depth
        self._handles: dict[str, _DesktopHandle] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def ensure_session_desktop(self, session_id: UUID | str, *, geometry: str | None = None) -> RuntimeDesktop:
        sid = str(session_id)
        target_geometry = geometry or self._geometry
        lock = self._locks.setdefault(sid, asyncio.Lock())
        async with lock:
            existing = self._handles.get(sid)
            if existing is not None:
                if geometry is None or existing.desktop.geometry == target_geometry:
                    return existing.desktop
                await _close_listener(existing.listener)
                self._handles.pop(sid, None)
                await self._ssh.run_script(
                    _build_stop_desktop_script(sid, root=self._workspaces_root),
                    timeout=30,
                )

            await self._terminal_manager.prepare_workspace(sid)
            result = await self._ssh.run_script(
                _build_ensure_desktop_script(
                    sid,
                    root=self._workspaces_root,
                    geometry=target_geometry,
                    depth=self._depth,
                ),
                timeout=45,
            )
            if result.exit_status not in {0, None}:
                detail = (result.stderr or result.stdout or "desktop start failed").strip()[:1200]
                raise RuntimeDesktopError(detail)
            try:
                payload = json.loads(result.stdout or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeDesktopError("Desktop start response was not valid JSON.") from exc
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                raise RuntimeDesktopError(str(payload.get("detail") or "Desktop start failed."))

            display_number = int(payload["display"])
            target_port = int(payload["port"])
            local_port = _allocate_local_port()
            try:
                listener = await self._ssh.forward_local_port(
                    "127.0.0.1",
                    local_port,
                    "127.0.0.1",
                    target_port,
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeDesktopError(f"Failed to open VNC SSH tunnel: {exc}") from exc

            desktop = RuntimeDesktop(
                session_id=sid,
                display=f":{display_number}",
                target_host="127.0.0.1",
                target_port=target_port,
                geometry=str(payload.get("geometry") or self._geometry),
                local_host="127.0.0.1",
                local_port=local_port,
            )
            self._handles[sid] = _DesktopHandle(desktop=desktop, listener=listener)
            return desktop

    async def get_session_desktop(self, session_id: UUID | str) -> RuntimeDesktop:
        sid = str(session_id)
        existing = self._handles.get(sid)
        if existing is not None:
            return existing.desktop
        return await self.ensure_session_desktop(sid)

    async def close_session(self, session_id: UUID | str) -> None:
        sid = str(session_id)
        lock = self._locks.setdefault(sid, asyncio.Lock())
        async with lock:
            handle = self._handles.pop(sid, None)
            if handle is not None:
                await _close_listener(handle.listener)
            await self._ssh.run_script(
                _build_stop_desktop_script(sid, root=self._workspaces_root),
                timeout=30,
            )

    async def close_all(self) -> None:
        for sid in list(self._handles):
            await self.close_session(sid)


async def _close_listener(listener: object) -> None:
    close = getattr(listener, "close", None)
    if callable(close):
        close()
    wait_closed = getattr(listener, "wait_closed", None)
    if callable(wait_closed):
        maybe_coro = wait_closed()
        if asyncio.iscoroutine(maybe_coro):
            await maybe_coro


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "infra" / "runtime" / "ansible" / "sentinel-runtime.yml").exists():
            return candidate
    raise FileNotFoundError("Could not locate Sentinel repository root.")


def ansible_repair_command() -> list[str]:
    from app.config import settings

    host = settings.runtime_ssh_host.strip()
    username = settings.runtime_ssh_username.strip()
    if not host or not username:
        raise RuntimeDesktopError("Runtime SSH host and username must be configured.")
    if settings.runtime_ssh_password.strip() and not settings.runtime_ssh_key_path.strip():
        raise RuntimeDesktopError("Runtime repair with password auth is not supported yet; use an SSH key.")

    playbook = find_repo_root() / "infra" / "runtime" / "ansible" / "sentinel-runtime.yml"
    command = [
        "ansible-playbook",
        "-i",
        f"{host},",
        str(playbook),
        "-u",
        username,
        "-e",
        f"ansible_port={int(settings.runtime_ssh_port)}",
    ]
    key_path = settings.runtime_ssh_key_path.strip()
    if key_path:
        command.extend(["--private-key", str(Path(key_path).expanduser())])
    return command


async def run_ansible_repair() -> dict[str, object]:
    command = ansible_repair_command()
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(find_repo_root()),
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "status": "unavailable",
            "command": command,
            "detail": "ansible-playbook is not available in the backend environment.",
        }

    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=900)
    return {
        "ok": process.returncode == 0,
        "status": "completed" if process.returncode == 0 else "failed",
        "command": command,
        "stdout": stdout.decode("utf-8", errors="replace")[-8000:] or None,
        "stderr": stderr.decode("utf-8", errors="replace")[-8000:] or None,
        "detail": None if process.returncode == 0 else f"ansible-playbook exited with {process.returncode}",
    }


def _build_ensure_desktop_script(
    session_id: str,
    *,
    root: str | None,
    geometry: str,
    depth: int,
) -> str:
    paths = workspace_paths(session_id, root=root)
    request = {
        "session_id": paths.session_id,
        "session_root": paths.session_root,
        "home": paths.home,
        "runtime": paths.runtime,
        "logs": paths.logs,
        "geometry": geometry,
        "depth": depth,
    }
    return _REMOTE_DESKTOP_ENSURE_SCRIPT.replace(
        "__SENTINEL_DESKTOP_REQUEST__",
        quote(json.dumps(request, separators=(",", ":"))),
    )


def _build_stop_desktop_script(session_id: str, *, root: str | None) -> str:
    paths = workspace_paths(session_id, root=root)
    request = {
        "home": paths.home,
        "runtime": paths.runtime,
    }
    return _REMOTE_DESKTOP_STOP_SCRIPT.replace(
        "__SENTINEL_DESKTOP_REQUEST__",
        quote(json.dumps(request, separators=(",", ":"))),
    )


_REMOTE_DESKTOP_ENSURE_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail
python3 - __SENTINEL_DESKTOP_REQUEST__ <<'PY'
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
"""


_REMOTE_DESKTOP_STOP_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail
python3 - __SENTINEL_DESKTOP_REQUEST__ <<'PY'
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REQ = json.loads(sys.argv[1])
HOME = Path(REQ["home"])
RUNTIME = Path(REQ["runtime"])
META = RUNTIME / "desktop.json"
WORKSPACE_XDG_RUNTIME_DIR = RUNTIME / "xdg"
TMP_XDG_RUNTIME_ROOT = Path("/tmp/sentinel-runtime")


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
            import shutil

            shutil.rmtree(candidate)

if META.exists():
    try:
        data = json.loads(META.read_text())
        display = int(data.get("display"))
        xdg_runtime_dir = data.get("xdg_runtime_dir")
    except Exception:
        display = None
        xdg_runtime_dir = None
    if display is not None:
        env = os.environ.copy()
        env["HOME"] = str(HOME)
        subprocess.run(["vncserver", "-kill", f":{display}"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    remove_xdg_runtime_dir(xdg_runtime_dir)
    META.unlink(missing_ok=True)

remove_xdg_runtime_dir(WORKSPACE_XDG_RUNTIME_DIR)
PY
"""
