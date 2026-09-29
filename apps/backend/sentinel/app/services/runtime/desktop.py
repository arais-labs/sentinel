"""On-demand graphical desktop shared by sessions attached to one workspace."""

from __future__ import annotations

import asyncio
import json
from importlib.resources import files
from dataclasses import dataclass
from uuid import UUID, uuid4

from app.services.runtime.guest_commands import guest_python_command
from app.services.runtime.desktop_appearance import desktop_default_files
from app.services.runtime.desktop_keyboard import host_keyboard
from app.services.runtime.terminal_manager import RuntimeTerminalManager
from app.services.runtime.workspace import WorkspaceLocation
from app.services.runtime import workspace_containers


class RuntimeDesktopError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeDesktop:
    session_id: str
    display: str
    target_host: str
    target_port: int
    geometry: str
    socket_path: str


@dataclass(slots=True)
class _DesktopHandle:
    desktop: RuntimeDesktop
    listener: object


class RuntimeDesktopManager:
    def __init__(
        self,
        terminal_manager: RuntimeTerminalManager,
        *,
        workspace_location: WorkspaceLocation,
        geometry: str = "1920x1200",
        depth: int = 24,
    ) -> None:
        self._transport = terminal_manager.ssh
        self._workspace_location = workspace_location
        self._geometry = geometry
        self._handles: dict[str, _DesktopHandle] = {}
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._workspace_location.desktop in {"xfce", "weston", "lxqt", "gnome", "plasma"}

    async def _command(self, action: str, geometry: str | None = None) -> dict:
        result = await self._transport.run(
            guest_python_command(
                "linux/desktop/control.py",
                [
                    json.dumps(
                        {
                            "action": action,
                            "geometry": geometry,
                            "defaults": desktop_default_files() if action == "start" else {},
                            "keyboard": await host_keyboard() if action == "start" else None,
                        }
                    )
                ],
            ),
            # Native PAM/compositor startup has a bounded 60-second readiness
            # window. Keep the transport alive through that window and cleanup.
            timeout=90 if action == "start" else 25,
        )
        try:
            payload = json.loads(result.stdout or "{}")
        except ValueError as exc:
            raise RuntimeDesktopError("Invalid workspace desktop response.") from exc
        if result.exit_status not in (0, None) or not payload.get("ok"):
            raise RuntimeDesktopError(
                payload.get("reason") or result.stderr or "Desktop operation failed."
            )
        return payload

    async def status(self) -> dict:
        if not self.enabled:
            return {
                "state": "not_installed",
                "reason": "Add Desktop to this workspace to use graphical applications.",
            }
        workspace = await self._transport.workspace_status()
        if workspace.get("state") != "running":
            state = workspace.get("state", "stopped")
            return {
                "state": "workspace_stopped" if state == "stopped" else state,
                "reason": workspace.get("error")
                or workspace.get("message")
                or "Start the workspace to use its desktop.",
            }
        return await self._command("status")

    async def _connect(self, session_id: str, payload: dict) -> RuntimeDesktop:
        existing = self._handles.get(session_id)
        socket_path, listener = await self._transport.desktop_socket(
            payload["port"], existing.listener if existing else None
        )
        if existing and existing.listener is not listener:
            await _close_listener(existing.listener)
        desktop = RuntimeDesktop(
            session_id,
            payload["display"],
            "127.0.0.1",
            payload["port"],
            payload["geometry"],
            socket_path,
        )
        self._handles[session_id] = _DesktopHandle(desktop, listener)
        return desktop

    async def ensure_session_desktop(
        self, session_id: UUID | str, *, geometry: str | None = None
    ) -> RuntimeDesktop:
        async with self._lock:
            if not self.enabled:
                raise RuntimeDesktopError(
                    "Add the Desktop package in workspace settings before starting graphical applications."
                )
            if not await self._transport.is_ready():
                raise RuntimeDesktopError("Start the workspace before starting its desktop.")
            await self._ensure_graphics(geometry)
            payload = await self._command("status") if geometry is None else {}
            if payload.get("state") != "running":
                payload = await self._command("start", geometry or self._geometry)
            if payload.get("state") != "running":
                raise RuntimeDesktopError(payload.get("reason") or "Desktop did not start.")
            return await self._connect(str(session_id), payload)

    async def get_session_desktop(
        self, session_id: UUID | str, *, status: dict | None = None
    ) -> RuntimeDesktop:
        # Viewing/reconnecting never starts a stopped desktop.
        async with self._lock:
            payload = status if status is not None else await self.status()
            if payload.get("state") != "running":
                raise RuntimeDesktopError(
                    payload.get("reason") or "Desktop is stopped. Start it to connect."
                )
            await self._ensure_graphics()
            return await self._connect(str(session_id), payload)

    async def computer(self, session_id: UUID | str, request: dict) -> dict:
        """Execute only inside this workspace's existing container transport."""
        await self.ensure_session_desktop(session_id)
        request_id = str(uuid4())
        task = asyncio.create_task(
            self._transport.run(
                guest_python_command(
                    "linux/desktop/computer.py", [json.dumps({**request, "request_id": request_id})]
                ),
                timeout=35,
            )
        )
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(
                    self._transport.run(
                        guest_python_command("linux/desktop/cancel_computer.py", [request_id]),
                        timeout=5,
                    )
                )
            finally:
                # Observe completion even if the caller has gone away.
                task.add_done_callback(
                    lambda done: done.exception() if not done.cancelled() else None
                )
            raise
        try:
            payload = json.loads(result.stdout or "{}")
        except ValueError as exc:
            raise RuntimeDesktopError(
                "Invalid workspace computer response; observe before retrying"
            ) from exc
        if not isinstance(payload, dict) or "ok" not in payload:
            raise RuntimeDesktopError(
                "Workspace computer control is unavailable. Reinstall the workspace desktop."
            )
        # Keep partial completion and the screenshot visible instead of hiding
        # them in an exception that could encourage replay of completed actions.
        return payload

    async def _ensure_graphics(self, geometry: str | None = None) -> None:
        width, height = map(int, (geometry or self._geometry).split("x"))
        try:
            await workspace_containers.request(
                "display_start",
                workspace=self._workspace_location.workspace_id,
                width=width,
                height=height,
            )
        except workspace_containers.WorkspaceContainerError as exc:
            raise RuntimeDesktopError(f"Metal desktop graphics unavailable: {exc}") from exc

    async def clipboard(self, payload: dict) -> dict:
        if (await self.status()).get("state") != "running":
            raise RuntimeDesktopError("Start the desktop before sharing its clipboard")
        process = await self._transport.create_process(
            "python3 /opt/sentinel/desktop/desktop-clipboard.py",
            encoding=None,
            start_if_needed=False,
        )
        try:
            async with asyncio.timeout(15):
                data = json.dumps(payload).encode()
                for offset in range(0, len(data), 32768):
                    await process.send_input(data[offset : offset + 32768])
                await process.send_input(None)
                output = bytearray()
                while chunk := await process.stdout.read(65536):
                    output.extend(chunk)
                    if len(output) > 2_097_152:
                        raise RuntimeDesktopError("Clipboard response exceeded its limit")
                await process.wait()
                result = json.loads(output)
                if process.exit_status != 0 or not result.get("ok"):
                    raise RuntimeDesktopError(result.get("reason") or "Clipboard transfer failed")
                return result
        finally:
            process.terminate()

    async def wallpaper(self, design: str) -> dict:
        if design not in {"satin", "horizon", "geometry", "spectrum", "paper", "monochrome"}:
            raise RuntimeDesktopError("Unknown wallpaper")
        if (await self.status()).get("state") != "running":
            raise RuntimeDesktopError("Start the desktop before choosing its wallpaper")
        data = (
            files("app.services.runtime.guest_commands")
            .joinpath("linux", "desktop", "wallpapers", design + ".png")
            .read_bytes()
        )
        process = await self._transport.create_process(
            guest_python_command("linux/desktop/set_wallpaper.py", []),
            encoding=None,
            start_if_needed=False,
        )
        try:
            async with asyncio.timeout(45):
                for offset in range(0, len(data), 32768):
                    await process.send_input(data[offset : offset + 32768])
                await process.send_input(None)
                output = bytearray()
                while chunk := await process.stdout.read(65536):
                    output.extend(chunk)
                    if len(output) > 65536:
                        raise RuntimeDesktopError("Wallpaper response exceeded its limit")
                await process.wait()
                result = json.loads(output)
                if process.exit_status != 0 or not result.get("ok"):
                    raise RuntimeDesktopError(result.get("reason") or "Could not apply wallpaper")
                return result
        finally:
            process.terminate()

    async def stop(self) -> None:
        async with self._lock:
            # Do not resurrect a stopped/deleted workspace for cleanup.
            try:
                if await self._transport.is_ready():
                    await self._command("stop")
            except workspace_containers.WorkspaceContainerError as exc:
                raise RuntimeDesktopError(f"Desktop graphics could not stop: {exc}") from exc
            finally:
                for handle in self._handles.values():
                    await _close_listener(handle.listener)
                self._handles.clear()

    async def close_session(self, session_id: UUID | str, *, stop_remote: bool = True) -> None:
        # Session disposal closes its tunnel, not another session's shared desktop.
        async with self._lock:
            handle = self._handles.pop(str(session_id), None)
            if handle:
                await _close_listener(handle.listener)

    async def close_all(self, *, stop_remote: bool = True) -> None:
        for sid in list(self._handles):
            await self.close_session(sid, stop_remote=stop_remote)


async def _close_listener(listener: object) -> None:
    close = getattr(listener, "close", None)
    if callable(close):
        close()
    wait_closed = getattr(listener, "wait_closed", None)
    if callable(wait_closed):
        result = wait_closed()
        if asyncio.iscoroutine(result):
            await result
