from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import ManagerSessionLocal
from app.middleware.auth import ACCESS_TOKEN_COOKIE_NAME, TokenPayload, decode_and_validate_token
from app.models import Session
from app.schemas.runtime import (
    RuntimeActionResponse,
    RuntimeLiveViewResponse,
    RuntimeProviderInfoItemResponse,
    RuntimeProviderInfoResponse,
    RuntimeRepairResponse,
)
from app.services.araios.runtime_services import get_browser_pool
from app.services.runtime.desktop import RuntimeDesktopError, run_ansible_repair
from app.services.runtime.ssh_runtime import (
    get_runtime_desktop_manager,
    get_runtime_port_forward_manager,
    get_runtime_terminal_manager,
    runtime_configured,
)

logger = logging.getLogger(__name__)


def runtime_desktop_ws_url(request: Request, session_id: str) -> str:
    runtime_prefix = request.url.path.rsplit("/live-view", 1)[0]
    return f"{runtime_prefix}/live-view/{session_id}/rfb"


def validated_desktop_resolution(value: str | None, presets: set[str]) -> str | None:
    if value is None:
        return None
    geometry = value.strip().lower()
    if geometry in presets:
        return geometry
    return None


def runtime_provider_info(*, configured: bool) -> RuntimeProviderInfoResponse:
    items: list[RuntimeProviderInfoItemResponse] = []
    if settings.runtime_ssh_host.strip():
        items.append(
            RuntimeProviderInfoItemResponse(
                key="host",
                label="Host",
                value=f"{settings.runtime_ssh_host.strip()}:{int(settings.runtime_ssh_port)}",
            )
        )
    if settings.runtime_workspaces_dir.strip():
        items.append(
            RuntimeProviderInfoItemResponse(
                key="workspaces",
                label="Workspaces",
                value=settings.runtime_workspaces_dir,
            )
        )
    return RuntimeProviderInfoResponse(
        id="ssh",
        label="SSH",
        status="configured" if configured else "not_configured",
        summary="SSH/tmux runtime is configured." if configured else "SSH/tmux runtime is not configured.",
        items=items,
    )


async def require_runtime_session(
    session_id: str,
    *,
    user: TokenPayload,
    db: AsyncSession,
) -> UUID:
    try:
        sid = UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid session id.") from exc
    result = await db.execute(select(Session.id).where(Session.id == sid, Session.user_id == user.sub))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if not runtime_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSH runtime is not configured.")
    return sid


async def live_view_response(
    *,
    request: Request,
    session_id: str,
    user: TokenPayload,
    db: AsyncSession,
    geometry: str | None,
    resolution_presets: set[str],
) -> RuntimeLiveViewResponse:
    configured = runtime_configured()
    provider = runtime_provider_info(configured=configured)
    desktop_geometry = validated_desktop_resolution(geometry, resolution_presets)
    if geometry is not None and desktop_geometry is None:
        return RuntimeLiveViewResponse(
            enabled=True,
            available=False,
            mode="vnc-rfb",
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
    result = await db.execute(select(Session.id).where(Session.id == sid, Session.user_id == user.sub))
    if result.scalar_one_or_none() is None:
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
            reason="SSH runtime is not configured.",
            provider=provider,
        )
    try:
        desktop = await get_runtime_desktop_manager().ensure_session_desktop(str(sid), geometry=desktop_geometry)
    except RuntimeDesktopError as exc:
        return RuntimeLiveViewResponse(
            enabled=True,
            available=False,
            mode="vnc-rfb",
            reason=str(exc),
            provider=provider,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to prepare runtime desktop for session %s", session_id, exc_info=True)
        return RuntimeLiveViewResponse(
            enabled=True,
            available=False,
            mode="vnc-rfb",
            reason=f"Runtime desktop unavailable: {exc}",
            provider=provider,
        )
    return RuntimeLiveViewResponse(
        enabled=True,
        available=True,
        mode="vnc-rfb",
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
    user: TokenPayload,
    db: AsyncSession,
    geometry: str,
    resolution_presets: set[str],
) -> RuntimeLiveViewResponse:
    configured = runtime_configured()
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
    result = await db.execute(select(Session.id).where(Session.id == sid, Session.user_id == user.sub))
    if result.scalar_one_or_none() is None:
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
            mode="vnc-rfb",
            reason=f"Unsupported desktop resolution: {geometry}",
            provider=provider,
        )
    if not configured:
        return RuntimeLiveViewResponse(
            enabled=False,
            available=False,
            mode="none",
            reason="SSH runtime is not configured.",
            provider=provider,
        )
    try:
        desktop = await get_runtime_desktop_manager().ensure_session_desktop(str(sid), geometry=desktop_geometry)
    except RuntimeDesktopError as exc:
        return RuntimeLiveViewResponse(
            enabled=True,
            available=False,
            mode="vnc-rfb",
            reason=str(exc),
            provider=provider,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to set runtime desktop resolution for session %s", session_id, exc_info=True)
        return RuntimeLiveViewResponse(
            enabled=True,
            available=False,
            mode="vnc-rfb",
            reason=f"Runtime desktop unavailable: {exc}",
            provider=provider,
        )
    return RuntimeLiveViewResponse(
        enabled=True,
        available=True,
        mode="vnc-rfb",
        url=None,
        ws_url=runtime_desktop_ws_url(request, str(sid)),
        display=desktop.display,
        geometry=desktop.geometry,
        reason=None,
        provider=provider,
    )


async def bridge_runtime_desktop_rfb(
    *,
    websocket: WebSocket,
    session_id: UUID,
    db: AsyncSession,
) -> None:
    token = websocket.query_params.get("token") or websocket.cookies.get(ACCESS_TOKEN_COOKIE_NAME)
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return
    try:
        async with ManagerSessionLocal() as manager_db:
            user = await decode_and_validate_token(token, manager_db, expected_type="access")
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return
    result = await db.execute(select(Session.id).where(Session.id == session_id, Session.user_id == user.sub))
    if result.scalar_one_or_none() is None:
        await websocket.close(code=4004, reason="Session not found")
        return

    try:
        desktop = await get_runtime_desktop_manager().get_session_desktop(str(session_id))
    except Exception:
        logger.warning("runtime desktop websocket prepare failed for session %s", session_id, exc_info=True)
        await websocket.close(code=4005, reason="Runtime desktop unavailable")
        return

    requested_protocols = [
        protocol.strip()
        for protocol in (websocket.headers.get("sec-websocket-protocol") or "").split(",")
        if protocol.strip()
    ]
    await websocket.accept(subprotocol="binary" if "binary" in requested_protocols else None)
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.open_connection(desktop.local_host, desktop.local_port)

        async def client_to_vnc() -> None:
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

        async def vnc_to_client() -> None:
            while True:
                data = await reader.read(65536)
                if not data:
                    return
                await websocket.send_bytes(data)

        done, pending = await asyncio.wait(
            {
                asyncio.create_task(client_to_vnc()),
                asyncio.create_task(vnc_to_client()),
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
            await websocket.close(code=4005, reason="Runtime desktop bridge unavailable")
        except Exception:
            return
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def repair_runtime_response() -> RuntimeRepairResponse:
    try:
        return RuntimeRepairResponse(**(await run_ansible_repair()))
    except RuntimeDesktopError as exc:
        return RuntimeRepairResponse(ok=False, status="unavailable", detail=str(exc))


async def reset_runtime_browser_action(
    *,
    session_id: str,
    user: TokenPayload,
    db: AsyncSession,
) -> RuntimeActionResponse:
    sid = await require_runtime_session(session_id, user=user, db=db)
    result = await get_browser_pool().reset(str(sid))
    return RuntimeActionResponse(
        ok=True,
        action="browser_reset",
        session_id=sid,
        result=result,
    )


async def restart_runtime_desktop_action(
    *,
    session_id: str,
    user: TokenPayload,
    db: AsyncSession,
    geometry: str,
    resolution_presets: set[str],
) -> RuntimeActionResponse:
    sid = await require_runtime_session(session_id, user=user, db=db)
    desktop_geometry = validated_desktop_resolution(geometry, resolution_presets)
    if desktop_geometry is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported desktop resolution: {geometry}",
        )
    await get_browser_pool().remove(str(sid))
    await get_runtime_desktop_manager().close_session(str(sid))
    desktop = await get_runtime_desktop_manager().ensure_session_desktop(str(sid), geometry=desktop_geometry)
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


async def wipe_runtime_workspace_action(
    *,
    session_id: str,
    user: TokenPayload,
    db: AsyncSession,
) -> RuntimeActionResponse:
    sid = await require_runtime_session(session_id, user=user, db=db)
    await get_browser_pool().remove(str(sid))
    await get_runtime_port_forward_manager().close_session(str(sid))
    await get_runtime_desktop_manager().close_session(str(sid))
    manager = get_runtime_terminal_manager()
    await manager.delete_workspace(str(sid))
    await manager.prepare_workspace(str(sid))
    return RuntimeActionResponse(
        ok=True,
        action="workspace_wipe",
        session_id=sid,
        result={"workspace_prepared": True},
    )
