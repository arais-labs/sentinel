from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from app.services.browser.manager import BrowserManager
from app.services.runtime.desktop import RuntimeDesktopManager
from app.services.runtime.remote_commands import load_remote_command
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


@dataclass(frozen=True, slots=True)
class _BrowserRuntime:
    terminal_manager: RuntimeTerminalManager
    desktop_manager: RuntimeDesktopManager
    workspaces_root: str | None


type _BrowserKey = tuple[str, str]


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
        self._handles: dict[_BrowserKey, _BrowserHandle] = {}
        self._locks: dict[_BrowserKey, asyncio.Lock] = {}

    async def get(self, session_id: str, *, instance_name: str | None = None) -> BrowserManager:
        sid = str(session_id)
        key = _handle_key(sid, instance_name=instance_name)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            handle = self._handles.get(key)
            if handle is not None:
                try:
                    await handle.manager.ensure_connected()
                    return handle.manager
                except Exception:
                    await self._close_handle(handle)
                    self._handles.pop(key, None)
                    await self._stop_remote(sid, instance_name=instance_name)
            handle = await self._start_remote_with_retry(sid, instance_name=instance_name)
            self._handles[key] = handle
            return handle.manager

    async def reset(self, session_id: str, *, instance_name: str | None = None) -> dict[str, Any]:
        sid = str(session_id)
        key = _handle_key(sid, instance_name=instance_name)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            handle = self._handles.pop(key, None)
            if handle is not None:
                await self._close_handle(handle)
            await self._reset_remote(sid, instance_name=instance_name)
            handle = await self._start_remote_with_retry(sid, instance_name=instance_name)
            self._handles[key] = handle
            runtime = await self._runtime(instance_name=instance_name)
            state = await handle.manager.warmup()
            return {
                "reset": True,
                "profile_dir": str(
                    PurePosixPath(workspace_paths(sid, root=runtime.workspaces_root).browser)
                    / "chromium"
                ),
                "cdp_endpoint": handle.cdp_endpoint,
                **state,
            }

    async def remove(self, session_id: str, *, instance_name: str | None = None) -> None:
        sid = str(session_id)
        key = _handle_key(sid, instance_name=instance_name)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            handle = self._handles.pop(key, None)
            if handle is not None:
                await self._close_handle(handle)
            await self._stop_remote(sid, instance_name=instance_name)

    async def close_all(self) -> None:
        for instance_key, sid in list(self._handles):
            await self.remove(
                sid,
                instance_name=None if instance_key == "default" else instance_key,
            )

    async def _start_remote_with_retry(
        self, session_id: str, *, instance_name: str | None
    ) -> _BrowserHandle:
        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                return await self._start_remote(session_id, instance_name=instance_name)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                try:
                    await self._stop_remote(session_id, instance_name=instance_name)
                except Exception:
                    pass
                if attempt == 2:
                    break
        raise BrowserPoolError(f"Failed to connect to runtime browser: {last_exc}") from last_exc

    async def _start_remote(self, session_id: str, *, instance_name: str | None) -> _BrowserHandle:
        runtime = await self._runtime(instance_name=instance_name)
        desktop = await runtime.desktop_manager.ensure_session_desktop(session_id)
        await runtime.terminal_manager.prepare_workspace(session_id)
        script, args = _build_browser_start_script(
            session_id,
            root=runtime.workspaces_root,
            display=desktop.display,
            geometry=desktop.geometry,
        )
        result = await runtime.terminal_manager.ssh.run_script(
            script,
            args=args,
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
            listener = await runtime.terminal_manager.ssh.forward_local_port(
                "127.0.0.1",
                local_port,
                "127.0.0.1",
                remote_port,
            )
        except Exception as exc:  # noqa: BLE001
            await self._stop_remote(session_id, instance_name=instance_name)
            raise BrowserPoolError(f"Failed to open browser CDP SSH tunnel: {exc}") from exc

        cdp_endpoint = f"http://127.0.0.1:{local_port}"
        manager = self._manager_cls(
            cdp_endpoint=cdp_endpoint,
            user_data_dir=str(
                PurePosixPath(workspace_paths(session_id, root=runtime.workspaces_root).browser)
                / "chromium"
            ),
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

    async def _stop_remote(self, session_id: str, *, instance_name: str | None) -> None:
        runtime = await self._runtime(instance_name=instance_name)
        script, args = _build_browser_stop_script(session_id, root=runtime.workspaces_root)
        await runtime.terminal_manager.ssh.run_script(
            script,
            args=args,
            timeout=30,
        )

    async def _reset_remote(self, session_id: str, *, instance_name: str | None) -> None:
        runtime = await self._runtime(instance_name=instance_name)
        script, args = _build_browser_reset_script(session_id, root=runtime.workspaces_root)
        result = await runtime.terminal_manager.ssh.run_script(
            script,
            args=args,
            timeout=45,
        )
        if result.exit_status not in {0, None}:
            detail = (result.stderr or result.stdout or "browser reset failed").strip()[:1200]
            raise BrowserPoolError(detail)

    async def _runtime(self, *, instance_name: str | None) -> _BrowserRuntime:
        terminal_manager = self._terminal_manager or await get_runtime_terminal_manager(
            instance_name=instance_name
        )
        desktop_manager = self._desktop_manager or await get_runtime_desktop_manager(
            instance_name=instance_name
        )
        workspaces_root = self._workspaces_root
        if workspaces_root is None:
            workspaces_root = getattr(terminal_manager, "workspaces_root", None)
        return _BrowserRuntime(
            terminal_manager=terminal_manager,
            desktop_manager=desktop_manager,
            workspaces_root=workspaces_root,
        )

    async def _close_handle(self, handle: _BrowserHandle) -> None:
        try:
            await handle.manager.close()
        finally:
            await _close_listener(handle.listener)


def _handle_key(session_id: str, *, instance_name: str | None) -> _BrowserKey:
    instance_key = (instance_name or "").strip().lower() or "default"
    return (instance_key, session_id)


async def _close_listener(listener: object) -> None:
    close = getattr(listener, "close", None)
    if callable(close):
        close()
    wait_closed = getattr(listener, "wait_closed", None)
    if callable(wait_closed):
        maybe_coro = wait_closed()
        if asyncio.iscoroutine(maybe_coro):
            await maybe_coro


def _browser_request(
    session_id: str, *, root: str | None, display: str | None = None, geometry: str | None = None
) -> dict[str, object]:
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


def _build_browser_start_script(
    session_id: str, *, root: str | None, display: str, geometry: str
) -> tuple[str, list[str]]:
    return (
        load_remote_command("linux/browser/start.sh"),
        [
            json.dumps(
                _browser_request(session_id, root=root, display=display, geometry=geometry),
                separators=(",", ":"),
            )
        ],
    )


def _build_browser_stop_script(session_id: str, *, root: str | None) -> tuple[str, list[str]]:
    return (
        load_remote_command("linux/browser/stop.sh"),
        [json.dumps(_browser_request(session_id, root=root), separators=(",", ":"))],
    )


def _build_browser_reset_script(session_id: str, *, root: str | None) -> tuple[str, list[str]]:
    return (
        load_remote_command("linux/browser/reset.sh"),
        [json.dumps(_browser_request(session_id, root=root), separators=(",", ":"))],
    )
