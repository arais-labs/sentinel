from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Session
from app.schemas.runtime import (
    RuntimeActionResponse,
    RuntimeLiveViewResponse,
    RuntimeProviderInfoResponse,
)
from app.services.modules.runtime_services import get_browser_pool
from app.services.runtime.desktop import RuntimeDesktopError
from app.services.runtime.compatibility import RuntimeCompatibilityError
from app.services.runtime.ssh_runtime import (
    get_runtime_desktop_manager,
    get_runtime_port_forward_manager,
    get_runtime_terminal_manager,
    runtime_configured,
)

logger = logging.getLogger(__name__)


def runtime_desktop_ws_url(request: Request, session_id: str) -> str:
    runtime_prefix = request.url.path.rsplit("/runtime", 1)[0] + "/runtime"
    return f"{runtime_prefix}/live-view/{session_id}/stream"


def validated_desktop_resolution(value: str | None, presets: set[str]) -> str | None:
    if value is None:
        return None
    geometry = value.strip().lower()
    if geometry in presets:
        return geometry
    return None


def runtime_provider_info(*, configured: bool) -> RuntimeProviderInfoResponse:
    return RuntimeProviderInfoResponse(
        id="container",
        label="Workspace",
        status="configured" if configured else "not_configured",
        summary=(
            "Runs inside the attached workspace." if configured else "No workspace is attached."
        ),
        items=[],
    )


async def _runtime_session_exists(db: AsyncSession, session_id: UUID) -> bool:
    # These routes only borrow the database for authorization. Runtime startup and
    # WebSocket bridges can outlive a request by minutes (or hours).
    try:
        result = await db.execute(select(Session.id).where(Session.id == session_id))
        return result.scalar_one_or_none() is not None
    finally:
        await db.close()


async def require_runtime_session(
    session_id: str,
    *,
    instance_name: str,
    db: AsyncSession,
) -> UUID:
    try:
        sid = UUID(session_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid session id.",
        ) from exc
    if not await _runtime_session_exists(db, sid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if not await runtime_configured(session_id=session_id, instance_name=instance_name):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No workspace is attached.",
        )
    return sid


def _request_instance_name(request: Request) -> str:
    return str(
        getattr(request.state, "instance_name", request.path_params.get("instance_name", ""))
    )


async def live_view_response(
    *,
    request: Request,
    session_id: str,
    db: AsyncSession,
    geometry: str | None,
    resolution_presets: set[str],
) -> RuntimeLiveViewResponse:
    instance_name = _request_instance_name(request)
    configured = await runtime_configured(session_id=session_id, instance_name=instance_name)
    provider = runtime_provider_info(configured=configured)
    desktop_geometry = validated_desktop_resolution(geometry, resolution_presets)
    if geometry is not None and desktop_geometry is None:
        return RuntimeLiveViewResponse(
            enabled=True,
            available=False,
            mode="h264",
            reason=f"Unsupported desktop resolution: {geometry}",
            provider=provider,
        )
    try:
        sid = UUID(session_id)
    except ValueError:
        return RuntimeLiveViewResponse(
            enabled=False,
            available=False,
            mode="none",
            reason="Invalid session id.",
            provider=provider,
        )
    if not await _runtime_session_exists(db, sid):
        return RuntimeLiveViewResponse(
            enabled=False,
            available=False,
            mode="none",
            reason="Session not found.",
            provider=provider,
        )
    if not configured:
        return RuntimeLiveViewResponse(
            enabled=False,
            available=False,
            mode="none",
            reason="No workspace is attached.",
            provider=provider,
        )
    try:
        desktop_manager = await get_runtime_desktop_manager(
            session_id=session_id, instance_name=instance_name
        )
        desktop_state = await desktop_manager.status()
        if desktop_state["state"] != "running":
            return RuntimeLiveViewResponse(
                enabled=desktop_manager.enabled,
                available=False,
                mode="h264",
                state=desktop_state["state"],
                reason=desktop_state.get("reason"),
                provider=provider,
            )
        desktop = await desktop_manager.get_session_desktop(str(sid), status=desktop_state)
    except RuntimeDesktopError as exc:
        return RuntimeLiveViewResponse(
            enabled=True,
            available=False,
            mode="h264",
            reason=str(exc),
            provider=provider,
        )
    except RuntimeCompatibilityError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "failed to prepare runtime desktop for session %s",
            session_id,
            exc_info=True,
        )
        return RuntimeLiveViewResponse(
            enabled=True,
            available=False,
            mode="h264",
            reason=f"Machine desktop unavailable: {exc}",
            provider=provider,
        )
    return RuntimeLiveViewResponse(
        enabled=True,
        available=True,
        state="running",
        mode="h264",
        url=None,
        ws_url=runtime_desktop_ws_url(request, str(sid)),
        display=desktop.display,
        geometry=desktop.geometry,
        reason=None,
        provider=provider,
    )


async def set_live_view_resolution_response(
    *,
    request: Request,
    session_id: str,
    db: AsyncSession,
    geometry: str,
    resolution_presets: set[str],
) -> RuntimeLiveViewResponse:
    instance_name = _request_instance_name(request)
    configured = await runtime_configured(session_id=session_id, instance_name=instance_name)
    provider = runtime_provider_info(configured=configured)
    desktop_geometry = validated_desktop_resolution(geometry, resolution_presets)
    try:
        sid = UUID(session_id)
    except ValueError:
        return RuntimeLiveViewResponse(
            enabled=False,
            available=False,
            mode="none",
            reason="Invalid session id.",
            provider=provider,
        )
    if not await _runtime_session_exists(db, sid):
        return RuntimeLiveViewResponse(
            enabled=False,
            available=False,
            mode="none",
            reason="Session not found.",
            provider=provider,
        )
    if desktop_geometry is None:
        return RuntimeLiveViewResponse(
            enabled=True,
            available=False,
            mode="h264",
            reason=f"Unsupported desktop resolution: {geometry}",
            provider=provider,
        )
    if not configured:
        return RuntimeLiveViewResponse(
            enabled=False,
            available=False,
            mode="none",
            reason="No workspace is attached.",
            provider=provider,
        )
    try:
        desktop_manager = await get_runtime_desktop_manager(
            session_id=session_id, instance_name=instance_name
        )
        desktop = await desktop_manager.ensure_session_desktop(str(sid), geometry=desktop_geometry)
    except RuntimeCompatibilityError:
        raise
    except RuntimeDesktopError as exc:
        return RuntimeLiveViewResponse(
            enabled=True,
            available=False,
            mode="h264",
            reason=str(exc),
            provider=provider,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "failed to set runtime desktop resolution for session %s",
            session_id,
            exc_info=True,
        )
        return RuntimeLiveViewResponse(
            enabled=True,
            available=False,
            mode="h264",
            reason=f"Machine desktop unavailable: {exc}",
            provider=provider,
        )
    return RuntimeLiveViewResponse(
        enabled=True,
        available=True,
        state="running",
        mode="h264",
        url=None,
        ws_url=runtime_desktop_ws_url(request, str(sid)),
        display=desktop.display,
        geometry=desktop.geometry,
        reason=None,
        provider=provider,
    )


async def bridge_runtime_desktop_stream(
    *,
    websocket: WebSocket,
    session_id: UUID,
    db: AsyncSession,
) -> None:
    if not await _runtime_session_exists(db, session_id):
        await websocket.close(code=4004, reason="Session not found")
        return

    try:
        desktop_manager = await get_runtime_desktop_manager(
            session_id=session_id,
            instance_name=str(websocket.path_params["instance_name"]),
        )
        desktop = await desktop_manager.get_session_desktop(str(session_id))
    except Exception:
        logger.warning(
            "runtime desktop websocket prepare failed for session %s",
            session_id,
            exc_info=True,
        )
        await websocket.close(code=4005, reason="Machine desktop unavailable")
        return

    requested_protocols = [
        protocol.strip()
        for protocol in (websocket.headers.get("sec-websocket-protocol") or "").split(",")
        if protocol.strip()
    ]
    await websocket.accept(subprotocol="binary" if "binary" in requested_protocols else None)
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.open_unix_connection(desktop.socket_path)

        async def client_to_display() -> None:
            assert writer is not None
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                if message.get("bytes") is not None:
                    writer.write(message["bytes"])
                    await writer.drain()
                elif message.get("text") is not None:
                    writer.write(message["text"].encode("utf-8"))
                    await writer.drain()

        async def display_to_client() -> None:
            while True:
                data = await reader.read(65536)
                if not data:
                    return
                await websocket.send_bytes(data)

        done, pending = await asyncio.wait(
            {
                asyncio.create_task(client_to_display()),
                asyncio.create_task(display_to_client()),
            },
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    except WebSocketDisconnect:
        return
    except Exception:
        logger.warning("runtime desktop websocket bridge failed", exc_info=True)
        try:
            await websocket.close(code=4005, reason="Machine desktop bridge unavailable")
        except Exception:
            return
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def reset_runtime_browser_action(
    *,
    session_id: str,
    instance_name: str,
    db: AsyncSession,
) -> RuntimeActionResponse:
    sid = await require_runtime_session(session_id, instance_name=instance_name, db=db)
    result = await get_browser_pool().reset(str(sid), instance_name=instance_name)
    return RuntimeActionResponse(
        ok=True,
        action="browser_reset",
        session_id=sid,
        result=result,
    )


async def restart_runtime_desktop_action(
    *,
    session_id: str,
    instance_name: str,
    db: AsyncSession,
    geometry: str,
    resolution_presets: set[str],
) -> RuntimeActionResponse:
    sid = await require_runtime_session(session_id, instance_name=instance_name, db=db)
    desktop_geometry = validated_desktop_resolution(geometry, resolution_presets)
    if desktop_geometry is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported desktop resolution: {geometry}",
        )
    await get_browser_pool().remove(str(sid), instance_name=instance_name)
    desktop_manager = await get_runtime_desktop_manager(
        session_id=session_id, instance_name=instance_name
    )
    await desktop_manager.stop()
    desktop = await desktop_manager.ensure_session_desktop(str(sid), geometry=desktop_geometry)
    return RuntimeActionResponse(
        ok=True,
        action="desktop_restart",
        session_id=sid,
        result={
            "display": desktop.display,
            "geometry": desktop.geometry,
            "target_port": desktop.target_port,
        },
    )


async def reset_runtime_session_action(
    *,
    session_id: str,
    instance_name: str,
    db: AsyncSession,
) -> RuntimeActionResponse:
    sid = await require_runtime_session(session_id, instance_name=instance_name, db=db)
    await get_browser_pool().remove(str(sid), instance_name=instance_name)
    forwards = await get_runtime_port_forward_manager(
        session_id=session_id, instance_name=instance_name
    )
    await forwards.close_session(str(sid))
    desktop = await get_runtime_desktop_manager(session_id=session_id, instance_name=instance_name)
    await desktop.close_session(str(sid))
    manager = await get_runtime_terminal_manager(session_id=session_id, instance_name=instance_name)
    await manager.delete_session_state(str(sid))
    await manager.prepare_workspace(str(sid))
    return RuntimeActionResponse(
        ok=True,
        action="session_reset",
        session_id=sid,
        result={"session_prepared": True},
    )
