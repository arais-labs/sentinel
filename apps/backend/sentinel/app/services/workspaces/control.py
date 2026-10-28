from __future__ import annotations

import logging
from uuid import UUID

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Session
from app.schemas.session_workspace import (
    WorkspaceActionResponse,
    WorkspaceDesktopResponse,
    WorkspaceProviderInfoResponse,
)
from app.services.modules.runtime_services import get_browser_pool
from app.services.workspaces.desktop.manager import DesktopError
from app.services.workspaces.compatibility import RuntimeCompatibilityError
from app.services.workspaces.managers import (
    get_desktop_manager,
    get_forward_manager,
    get_terminal_manager,
    workspace_attached,
)

logger = logging.getLogger(__name__)


def workspace_provider_info(*, configured: bool) -> WorkspaceProviderInfoResponse:
    return WorkspaceProviderInfoResponse(
        id="vm",
        label="Workspace",
        status="configured" if configured else "not_configured",
        summary=(
            "Runs inside the attached workspace." if configured else "No workspace is attached."
        ),
        items=[],
    )


async def _session_exists(db: AsyncSession, session_id: UUID) -> bool:
    # These routes only borrow the database for authorization; runtime startup can outlive a
    # request by minutes.
    try:
        result = await db.execute(select(Session.id).where(Session.id == session_id))
        return result.scalar_one_or_none() is not None
    finally:
        await db.close()


async def require_workspace_session(
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
    if not await _session_exists(db, sid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if not await workspace_attached(session_id=session_id, instance_name=instance_name):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No workspace is attached.",
        )
    return sid


def _request_instance_name(request: Request) -> str:
    return str(
        getattr(request.state, "instance_name", request.path_params.get("instance_name", ""))
    )


async def desktop_response(
    *,
    request: Request,
    session_id: str,
    db: AsyncSession,
) -> WorkspaceDesktopResponse:
    instance_name = _request_instance_name(request)
    configured = await workspace_attached(session_id=session_id, instance_name=instance_name)
    provider = workspace_provider_info(configured=configured)
    try:
        sid = UUID(session_id)
    except ValueError:
        return WorkspaceDesktopResponse(
            enabled=False,
            available=False,
            mode="none",
            reason="Invalid session id.",
            provider=provider,
        )
    if not await _session_exists(db, sid):
        return WorkspaceDesktopResponse(
            enabled=False,
            available=False,
            mode="none",
            reason="Session not found.",
            provider=provider,
        )
    if not configured:
        return WorkspaceDesktopResponse(
            enabled=False,
            available=False,
            mode="none",
            reason="No workspace is attached.",
            provider=provider,
        )
    try:
        desktop_manager = await get_desktop_manager(
            session_id=session_id, instance_name=instance_name
        )
        desktop_state = await desktop_manager.status()
        if desktop_state["state"] != "running":
            return WorkspaceDesktopResponse(
                enabled=desktop_manager.enabled,
                available=False,
                mode="h264",
                state=desktop_state["state"],
                reason=desktop_state.get("reason"),
                viewable=bool(desktop_state.get("viewable")),
                provider=provider,
            )
        desktop = await desktop_manager.get_session_desktop(str(sid), status=desktop_state)
    except DesktopError as exc:
        return WorkspaceDesktopResponse(
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
        return WorkspaceDesktopResponse(
            enabled=True,
            available=False,
            mode="h264",
            reason=f"Machine desktop unavailable: {exc}",
            provider=provider,
        )
    return WorkspaceDesktopResponse(
        enabled=True,
        available=True,
        state="running",
        viewable=True,
        mode="h264",
        display=desktop.display,
        geometry=desktop.geometry,
        reason=None,
        provider=provider,
    )


async def reset_workspace_browser_action(
    *,
    session_id: str,
    instance_name: str,
    db: AsyncSession,
) -> WorkspaceActionResponse:
    sid = await require_workspace_session(session_id, instance_name=instance_name, db=db)
    result = await get_browser_pool().reset(str(sid), instance_name=instance_name)
    return WorkspaceActionResponse(
        ok=True,
        action="browser_reset",
        session_id=sid,
        result=result,
    )


async def reset_workspace_session_action(
    *,
    session_id: str,
    instance_name: str,
    db: AsyncSession,
) -> WorkspaceActionResponse:
    sid = await require_workspace_session(session_id, instance_name=instance_name, db=db)
    await get_browser_pool().remove(str(sid), instance_name=instance_name)
    forwards = await get_forward_manager(session_id=session_id, instance_name=instance_name)
    await forwards.close_session(str(sid))
    manager = await get_terminal_manager(session_id=session_id, instance_name=instance_name)
    await manager.delete_session_state(str(sid))
    await manager.prepare_workspace(str(sid))
    return WorkspaceActionResponse(
        ok=True,
        action="session_reset",
        session_id=sid,
        result={"session_prepared": True},
    )
