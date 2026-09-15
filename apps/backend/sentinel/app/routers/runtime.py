from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.runtime.control as control_module
import app.services.runtime.ssh_runtime as ssh_runtime_module
from app.dependencies import get_db
from app.schemas.runtime import (
    RuntimeActionResponse,
    RuntimeDesktopResolutionRequest,
    RuntimeLiveViewResponse,
    RuntimeStatusResponse,
)
from app.services.runtime.desktop import RuntimeDesktopError
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
    session_id: str | None = Query(None),
) -> RuntimeStatusResponse:
    return RuntimeStatusResponse(
        **(
            await runtime_status_payload(
                instance_name=_request_instance_name(request), session_id=session_id
            )
        )
    )


@router.get("/live-view", response_model=RuntimeLiveViewResponse)
async def get_live_view(
    request: Request,
    session_id: str = Query(..., description="Session UUID"),
    geometry: str | None = Query(None, description="Optional desktop framebuffer geometry"),
    db: AsyncSession = Depends(get_db),
) -> RuntimeLiveViewResponse:
    return await control_module.live_view_response(
        request=request,
        session_id=session_id,
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
    await control_module.bridge_runtime_desktop_rfb(
        websocket=websocket, session_id=session_id, db=db
    )


@router.post("/live-view/{session_id}/rfb")
async def runtime_desktop_connection(
    request: Request,
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    # The authenticated backend resolves the workspace. The renderer never
    # supplies a filesystem path or SSH destination to Electron.

    if not await control_module._runtime_session_exists(db, session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        manager = await ssh_runtime_module.get_runtime_desktop_manager(
            session_id=session_id, instance_name=_request_instance_name(request)
        )
        desktop = await manager.get_session_desktop(session_id)
    except RuntimeDesktopError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"socket": desktop.socket_path}


@router.post("/live-view/resolution", response_model=RuntimeLiveViewResponse)
async def set_live_view_resolution(
    request: Request,
    payload: RuntimeDesktopResolutionRequest,
    session_id: str = Query(..., description="Session UUID"),
    db: AsyncSession = Depends(get_db),
) -> RuntimeLiveViewResponse:
    return await control_module.set_live_view_resolution_response(
        request=request,
        session_id=session_id,
        db=db,
        geometry=payload.geometry,
        resolution_presets=DESKTOP_RESOLUTION_PRESETS,
    )


@router.post("/browser/reset", response_model=RuntimeActionResponse)
async def reset_runtime_browser(
    request: Request,
    session_id: str = Query(..., description="Session UUID"),
    db: AsyncSession = Depends(get_db),
) -> RuntimeActionResponse:
    return await control_module.reset_runtime_browser_action(
        session_id=session_id,
        instance_name=_request_instance_name(request),
        db=db,
    )


@router.post("/desktop/restart", response_model=RuntimeActionResponse)
async def restart_runtime_desktop(
    payload: RuntimeDesktopResolutionRequest,
    request: Request,
    session_id: str = Query(..., description="Session UUID"),
    db: AsyncSession = Depends(get_db),
) -> RuntimeActionResponse:
    return await control_module.restart_runtime_desktop_action(
        session_id=session_id,
        instance_name=_request_instance_name(request),
        db=db,
        geometry=payload.geometry,
        resolution_presets=DESKTOP_RESOLUTION_PRESETS,
    )


@router.post("/session/reset", response_model=RuntimeActionResponse)
async def reset_runtime_session(
    request: Request,
    session_id: str = Query(..., description="Session UUID"),
    db: AsyncSession = Depends(get_db),
) -> RuntimeActionResponse:
    return await control_module.reset_runtime_session_action(
        session_id=session_id,
        instance_name=_request_instance_name(request),
        db=db,
    )


def _request_instance_name(request: Request) -> str:
    return str(
        getattr(request.state, "instance_name", request.path_params.get("instance_name", ""))
    )


@router.post("/desktop/start", response_model=RuntimeLiveViewResponse)
async def start_workspace_desktop(
    request: Request,
    payload: RuntimeDesktopResolutionRequest,
    session_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    await control_module.require_runtime_session(
        session_id, instance_name=_request_instance_name(request), db=db
    )
    return await control_module.set_live_view_resolution_response(
        request=request,
        session_id=session_id,
        db=db,
        geometry=payload.geometry,
        resolution_presets=DESKTOP_RESOLUTION_PRESETS,
    )


@router.post("/desktop/stop")
async def stop_workspace_desktop(
    request: Request, session_id: str = Query(...), db: AsyncSession = Depends(get_db)
):

    await control_module.require_runtime_session(
        session_id, instance_name=_request_instance_name(request), db=db
    )
    manager = await ssh_runtime_module.get_runtime_desktop_manager(
        session_id=session_id, instance_name=_request_instance_name(request)
    )
    try:
        await manager.stop()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not stop desktop: {exc}") from exc
    return {"ok": True, "state": "stopped"}
