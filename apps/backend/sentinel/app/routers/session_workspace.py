from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Literal

import app.services.workspaces.control as control_module
from app.services.workspaces import managers
from app.dependencies import get_db
from app.schemas.session_workspace import (
    WorkspaceActionResponse,
    WorkspaceDesktopResponse,
    WorkspaceStatusResponse,
)
from app.services.workspaces.desktop.manager import DesktopError
from app.services.workspaces.machines.status import workspace_status_payload

# The workspace attached to a session: its status, desktop, browser and shell state.
router = APIRouter(prefix="/sessions/{session_id}/workspace")


class DesktopClipboardRequest(BaseModel):
    action: Literal["read", "write"]
    text: str | None = Field(default=None, max_length=262144)


class DesktopWallpaperRequest(BaseModel):
    # One of sentineld's catalog wallpapers; sentineld rejects any other.
    design: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")


@router.post("/desktop/wallpaper")
async def desktop_wallpaper(
    payload: DesktopWallpaperRequest,
    request: Request,
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    await control_module.require_workspace_session(
        str(session_id), instance_name=_request_instance_name(request), db=db
    )
    manager = await managers.get_desktop_manager(
        session_id=str(session_id), instance_name=_request_instance_name(request)
    )
    try:
        return await manager.wallpaper(payload.design)
    except DesktopError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/desktop/clipboard")
async def desktop_clipboard(
    payload: DesktopClipboardRequest,
    request: Request,
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    await control_module.require_workspace_session(
        str(session_id), instance_name=_request_instance_name(request), db=db
    )
    manager = await managers.get_desktop_manager(
        session_id=str(session_id), instance_name=_request_instance_name(request)
    )
    try:
        return await manager.clipboard(payload.model_dump())
    except DesktopError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/status", response_model=WorkspaceStatusResponse)
async def get_session_workspace_status(
    request: Request,
    session_id: UUID,
) -> WorkspaceStatusResponse:
    return WorkspaceStatusResponse(
        **(
            await workspace_status_payload(
                instance_name=_request_instance_name(request), session_id=str(session_id)
            )
        )
    )


@router.get("/desktop", response_model=WorkspaceDesktopResponse)
async def get_workspace_desktop(
    request: Request,
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> WorkspaceDesktopResponse:
    return await control_module.desktop_response(request=request, session_id=str(session_id), db=db)


@router.post("/desktop/viewer")
async def desktop_viewer(
    request: Request,
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    # The authenticated backend resolves the workspace and returns a single-use viewer ticket
    # for sentineld's WebTransport endpoint (port, certificate hash, ticket).

    if not await control_module._session_exists(db, session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        manager = await managers.get_desktop_manager(
            session_id=str(session_id), instance_name=_request_instance_name(request)
        )
        return await manager.viewer(session_id)
    except DesktopError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/browser/reset", response_model=WorkspaceActionResponse)
async def reset_workspace_browser(
    request: Request,
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> WorkspaceActionResponse:
    return await control_module.reset_workspace_browser_action(
        session_id=str(session_id),
        instance_name=_request_instance_name(request),
        db=db,
    )


@router.post("/reset", response_model=WorkspaceActionResponse)
async def reset_workspace_session(
    request: Request,
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> WorkspaceActionResponse:
    return await control_module.reset_workspace_session_action(
        session_id=str(session_id),
        instance_name=_request_instance_name(request),
        db=db,
    )


def _request_instance_name(request: Request) -> str:
    return str(
        getattr(request.state, "instance_name", request.path_params.get("instance_name", ""))
    )
