from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.services.runtime.remote_commands import load_remote_command
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
            environment = await self._terminal_manager.runtime_environment()
            if environment.os != "linux":
                raise RuntimeDesktopError("Desktop live view is currently supported only on Linux SSH targets.")
            existing = self._handles.get(sid)
            if existing is not None:
                if geometry is None or existing.desktop.geometry == target_geometry:
                    return existing.desktop
                await _close_listener(existing.listener)
                self._handles.pop(sid, None)
                script, args = _build_stop_desktop_script(sid, root=self._workspaces_root)
                await self._ssh.run_script(
                    script,
                    args=args,
                    timeout=30,
                )

            await self._terminal_manager.prepare_workspace(sid)
            script, args = _build_ensure_desktop_script(
                sid,
                root=self._workspaces_root,
                geometry=target_geometry,
                depth=self._depth,
            )
            result = await self._ssh.run_script(
                script,
                args=args,
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
            script, args = _build_stop_desktop_script(sid, root=self._workspaces_root)
            await self._ssh.run_script(
                script,
                args=args,
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


def _build_ensure_desktop_script(
    session_id: str,
    *,
    root: str | None,
    geometry: str,
    depth: int,
) -> tuple[str, list[str]]:
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
    return load_remote_command("linux/desktop/ensure.sh"), [json.dumps(request, separators=(",", ":"))]


def _build_stop_desktop_script(session_id: str, *, root: str | None) -> tuple[str, list[str]]:
    paths = workspace_paths(session_id, root=root)
    request = {
        "home": paths.home,
        "runtime": paths.runtime,
    }
    return load_remote_command("linux/desktop/stop.sh"), [json.dumps(request, separators=(",", ":"))]
