"""On-demand graphical desktop shared by sessions attached to one workspace."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from app.services.workspaces.desktop.keyboard import host_keyboard
from app.services.workspaces.terminals.manager import TerminalManager
from app.services.workspaces.paths import WorkspaceLocation
from app.services.workspaces import lifecycle

logger = logging.getLogger(__name__)


class DesktopError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SessionDesktop:
    session_id: str
    display: str
    geometry: str


# The VM's display mode, fixed by sentineld (kDisplay in packages/sentineld/core/workspaces.cpp).
DISPLAY_GEOMETRY = "1920x1200"


class DesktopManager:
    """The desktop of a workspace that has one. It starts and stops with the workspace (sentineld
    provisions it); there is nothing to start, stop or resize separately."""

    def __init__(
        self,
        terminal_manager: TerminalManager,
        *,
        workspace_location: WorkspaceLocation,
    ) -> None:
        self._transport = terminal_manager.ssh
        self._workspace_location = workspace_location
        self._lock = asyncio.Lock()
        self._keyboard: dict | None = None  # the host layouts last given to the desktop

    @property
    def enabled(self) -> bool:
        # sentineld validated the desktop against its catalog.
        return self._workspace_location.desktop != "none"

    async def _session_state(self) -> dict:
        # The desktop runs with its workspace: sentineld reports it running only once its desktop
        # start has seen the display server up (DESIGN.md §8), so there is nothing more to check.
        return {"state": "running", "display": ":0", "geometry": DISPLAY_GEOMETRY}

    async def status(self) -> dict:
        if not self.enabled:
            return {
                "state": "not_installed",
                "reason": "Choose a desktop in workspace settings to use graphical applications.",
            }
        workspace = await self._transport.workspace_status()
        # The machine's screen can be watched from power on (firmware, boot, provisioning), before
        # the desktop runs.
        viewable = bool(workspace.get("screen"))
        if workspace.get("state") != "running":
            state = workspace.get("state", "stopped")
            return {
                "state": "workspace_stopped" if state == "stopped" else state,
                "reason": workspace.get("error")
                or workspace.get("message")
                or "Start the workspace to use its desktop.",
                "viewable": viewable,
            }
        return {**await self._session_state(), "viewable": viewable}

    async def _apply_keyboard(self) -> None:
        # The viewer sends physical keys: the desktop types with the host's layouts. Read on each
        # connect (the user may have changed them), sent only when they changed; sentineld keeps
        # them for the workspace and applies them again after every desktop start.
        keyboard = await host_keyboard()
        if keyboard is None or keyboard == self._keyboard:
            return
        try:
            await lifecycle.request(
                "keyboard", workspace=str(self._workspace_location.workspace_id), **keyboard
            )
        except lifecycle.WorkspaceLifecycleError as exc:
            # The desktop still works with its own layout; typing may differ from the Mac's.
            logger.warning("Desktop keyboard layout not applied: %s", str(exc)[-300:])
            return
        self._keyboard = keyboard

    async def _connect(self, session_id: str, payload: dict) -> SessionDesktop:
        await self._apply_keyboard()
        return SessionDesktop(session_id, payload["display"], payload["geometry"])

    async def viewer(self, session_id: UUID | str) -> dict:
        """A WebTransport ticket for the viewer: {port, cert_sha256, ticket} (sentineld display).
        Given while the desktop runs, and while the machine's screen is viewable as it starts."""
        status = await self.status()
        if status.get("state") == "running":
            await self.get_session_desktop(session_id, status=status)
        elif not status.get("viewable"):
            raise DesktopError(status.get("reason") or "The desktop is not running.")
        try:
            reply = await lifecycle.request(
                "display", workspace=str(self._workspace_location.workspace_id)
            )
        except lifecycle.WorkspaceLifecycleError as exc:
            raise DesktopError(f"Desktop viewer unavailable: {exc}") from exc
        return {key: reply[key] for key in ("port", "cert_sha256", "ticket")}

    async def ensure_session_desktop(self, session_id: UUID | str) -> SessionDesktop:
        async with self._lock:
            if not self.enabled:
                raise DesktopError(
                    "Choose a desktop in workspace settings to use graphical applications."
                )
            if not await self._transport.is_ready():
                raise DesktopError("Start the workspace to use its desktop.")
            payload = await self._session_state()
            if payload["state"] != "running":
                raise DesktopError(payload["reason"])
            return await self._connect(str(session_id), payload)

    async def get_session_desktop(
        self, session_id: UUID | str, *, status: dict | None = None
    ) -> SessionDesktop:
        # Viewing/reconnecting never starts a stopped desktop.
        async with self._lock:
            payload = status if status is not None else await self.status()
            if payload.get("state") != "running":
                raise DesktopError(payload.get("reason") or "The desktop is not running.")
            return await self._connect(str(session_id), payload)

    async def computer(self, session_id: UUID | str, request: dict) -> dict:
        """One action batch, run by sentineld on the workspace's display (its computer op)."""
        await self.ensure_session_desktop(session_id)
        workspace = str(self._workspace_location.workspace_id)
        values = {key: request[key] for key in ("actions", "viewport") if key in request}
        task = asyncio.create_task(lifecycle.request("computer", workspace=workspace, **values))
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(lifecycle.request("computer_cancel", workspace=workspace))
            finally:
                # Observe completion even if the caller has gone away.
                task.add_done_callback(
                    lambda done: done.exception() if not done.cancelled() else None
                )
            raise
        except lifecycle.WorkspaceLifecycleError as exc:
            raise DesktopError(f"Workspace computer control is unavailable: {exc}") from exc
        # Partial completion and the screenshot stay visible instead of hiding them in an
        # exception that could encourage replay of completed actions.
        return result["batch"]

    async def clipboard(self, payload: dict) -> dict:
        if (await self.status()).get("state") != "running":
            raise DesktopError("Start the desktop before sharing its clipboard")
        # "direction", not sentineld's "action": the runtime requests name their operation "action".
        values = {"direction": payload["action"]}
        if payload["action"] == "write":
            values["text"] = payload.get("text") or ""
        try:
            reply = await lifecycle.request(
                "clipboard", workspace=str(self._workspace_location.workspace_id), **values
            )
        except lifecycle.WorkspaceLifecycleError as exc:
            raise DesktopError(f"Clipboard transfer failed: {exc}") from exc
        return {
            "ok": True,
            **({"text": reply.get("text", "")} if payload["action"] == "read" else {}),
        }

    async def wallpaper(self, design: str) -> dict:
        # The designs ship with sentineld (its catalog); the guest takes the image from its share.
        if (await self.status()).get("state") != "running":
            raise DesktopError("Start the desktop before choosing its wallpaper")
        try:
            await lifecycle.request(
                "wallpaper", workspace=str(self._workspace_location.workspace_id), design=design
            )
        except lifecycle.WorkspaceLifecycleError as exc:
            raise DesktopError(f"Could not apply wallpaper: {exc}") from exc
        return {"ok": True}
