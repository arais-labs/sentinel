from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.schemas.runtime import (
    RuntimeActionResponse,
    RuntimeDesktopResolutionRequest,
    RuntimeLiveViewResponse,
    RuntimeStatusResponse,
)
from app.services.runtime.control import (
    bridge_runtime_desktop_rfb,
    live_view_response,
    reset_runtime_browser_action,
    restart_runtime_desktop_action,
    set_live_view_resolution_response,
    wipe_runtime_workspace_action,
)
from app.services.runtime.status import runtime_status_payload

router = APIRouter()

DESKTOP_RESOLUTION_PRESETS = {
    "1280x800",
    "1440x900",
    "1680x1050",
    "1920x1200",
    "2560x1600",
    "2880x1800",
    "3840x2400",
}


@router.get("/status", response_model=RuntimeStatusResponse)
async def get_runtime_status(
    request: Request,
    _user: TokenPayload = Depends(require_auth),
) -> RuntimeStatusResponse:
    return RuntimeStatusResponse(
        **(await runtime_status_payload(instance_name=_request_instance_name(request)))
    )


@router.get("/live-view", response_model=RuntimeLiveViewResponse)
async def get_live_view(
    request: Request,
    session_id: str = Query(..., description="Session UUID"),
    geometry: str | None = Query(None, description="Optional desktop framebuffer geometry"),
    _user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> RuntimeLiveViewResponse:
    return await live_view_response(
        request=request,
        session_id=session_id,
        user=_user,
        db=db,
        geometry=geometry,
        resolution_presets=DESKTOP_RESOLUTION_PRESETS,
    )


@router.websocket("/live-view/{session_id}/rfb")
async def runtime_live_view_rfb(
    websocket: WebSocket,
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    await bridge_runtime_desktop_rfb(websocket=websocket, session_id=session_id, db=db)


@router.post("/live-view/resolution", response_model=RuntimeLiveViewResponse)
async def set_live_view_resolution(
    request: Request,
    payload: RuntimeDesktopResolutionRequest,
    session_id: str = Query(..., description="Session UUID"),
    _user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> RuntimeLiveViewResponse:
    return await set_live_view_resolution_response(
        request=request,
        session_id=session_id,
        user=_user,
        db=db,
        geometry=payload.geometry,
        resolution_presets=DESKTOP_RESOLUTION_PRESETS,
    )


@router.post("/browser/reset", response_model=RuntimeActionResponse)
async def reset_runtime_browser(
    request: Request,
    session_id: str = Query(..., description="Session UUID"),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> RuntimeActionResponse:
    return await reset_runtime_browser_action(
        session_id=session_id,
        instance_name=_request_instance_name(request),
        user=user,
        db=db,
    )


@router.post("/desktop/restart", response_model=RuntimeActionResponse)
async def restart_runtime_desktop(
    payload: RuntimeDesktopResolutionRequest,
    request: Request,
    session_id: str = Query(..., description="Session UUID"),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> RuntimeActionResponse:
    return await restart_runtime_desktop_action(
        session_id=session_id,
        instance_name=_request_instance_name(request),
        user=user,
        db=db,
        geometry=payload.geometry,
        resolution_presets=DESKTOP_RESOLUTION_PRESETS,
    )


@router.post("/workspace/wipe", response_model=RuntimeActionResponse)
async def wipe_runtime_workspace(
    request: Request,
    session_id: str = Query(..., description="Session UUID"),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> RuntimeActionResponse:
    return await wipe_runtime_workspace_action(
        session_id=session_id,
        instance_name=_request_instance_name(request),
        user=user,
        db=db,
    )


def _request_instance_name(request: Request) -> str:
    return str(
        getattr(request.state, "instance_name", request.path_params.get("instance_name", ""))
    )
