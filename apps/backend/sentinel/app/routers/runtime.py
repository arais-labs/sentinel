from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.config import settings
from app.middleware.auth import TokenPayload, require_auth
from app.schemas.runtime import (
    RuntimeResetResponse,
    RuntimeLiveViewResponse,
    RuntimeProviderInfoItemResponse,
    RuntimeProviderInfoResponse,
)
from app.services.runtime.runtime_live_view import (
    build_runtime_view_url,
    is_runtime_available_for_session,
)
from app.services.runtime.activation import queue_runtime_activation
from app.services.browser.pool import BrowserPool

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/live-view", response_model=RuntimeLiveViewResponse)
async def get_live_view(
    request: Request,
    session_id: str = Query(..., description="Session UUID for per-session VNC"),
    _user: TokenPayload = Depends(require_auth),
) -> RuntimeLiveViewResponse:
    provider_info = await _resolve_runtime_provider_info(session_id)
    if not settings.runtime_live_view_enabled:
        return RuntimeLiveViewResponse(
            enabled=False,
            available=False,
            url=None,
            reason="Live desktop view is disabled.",
            provider=provider_info,
        )
    url = build_runtime_view_url(request, session_id=session_id)
    available = is_runtime_available_for_session(session_id)
    return RuntimeLiveViewResponse(
        enabled=True,
        available=available,
        url=url,
        reason=None if available else "Runtime desktop is not reachable.",
        provider=provider_info,
    )


@router.post("/reset", response_model=RuntimeResetResponse)
async def reset_runtime(
    request: Request,
    session_id: str = Query(..., description="Session UUID"),
    _user: TokenPayload = Depends(require_auth),
) -> RuntimeResetResponse:
    """Reset the session browser and fall back to a hard Chromium restart if needed."""
    pool = _resolve_browser_pool(request)
    if pool is not None:
        try:
            result = await pool.reset(session_id)
            return RuntimeResetResponse(**result)
        except Exception as exc:
            logger.warning(
                "Browser pool reset failed for session %s; falling back to SSH Chromium restart",
                session_id,
                exc_info=True,
            )

    from app.services.runtime import get_runtime

    # Fallback: kill and restart Chromium via SSH when no browser pool is available.
    try:
        provider = get_runtime()
        inst = provider.get(session_id)
        if inst is None:
            raise HTTPException(status_code=404, detail="Runtime not found for session")
        await provider.restart_browser(session_id, inst)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to restart Chromium: {exc}") from exc

    return RuntimeResetResponse(reset=True, url="about:blank")


@router.post("/activate-session")
async def activate_runtime_session(
    request: Request,
    session_id: str = Query(..., description="Session UUID"),
    _user: TokenPayload = Depends(require_auth),
) -> dict[str, object]:
    pool = _resolve_browser_pool(request)
    if pool is not None:
        await pool.remove(session_id)

    queued = queue_runtime_activation(request.app, session_id)

    return {
        "activated": False,
        "queued": queued,
        "session_id": session_id,
    }


@router.post("/restart-container")
async def restart_container(
    request: Request,
    session_id: str = Query(..., description="Session UUID"),
    _user: TokenPayload = Depends(require_auth),
) -> dict:
    """Hard-restart the runtime backing this session and re-prepare the session."""
    import asyncio
    from app.services.runtime import get_runtime
    from app.services.ws.ws_manager import ConnectionManager

    pool: BrowserPool | None = getattr(request.app.state, "browser_pool", None)
    if pool is not None:
        await pool.remove(session_id)

    try:
        provider = get_runtime()
        ws: ConnectionManager | None = getattr(request.app.state, "ws_manager", None)
        asyncio.create_task(_hard_restart_runtime_task(provider, session_id, ws))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to restart container: {exc}") from exc

    return {"restarting": True}


async def _hard_restart_runtime_task(provider, session_id: str, ws) -> None:
    import logging
    logger = logging.getLogger(__name__)
    try:
        await provider.hard_restart(session_id)
        logger.info("Runtime hard-restarted for session %s", session_id)
        if ws is not None and hasattr(ws, "broadcast_runtime_ready"):
            await ws.broadcast_runtime_ready(session_id)
    except Exception:
        logger.warning("Runtime hard restart failed for session %s", session_id, exc_info=True)


def _resolve_browser_pool(request: Request) -> BrowserPool | None:
    pool = getattr(request.app.state, "browser_pool", None)
    if isinstance(pool, BrowserPool):
        return pool
    return None


async def _resolve_runtime_provider_info(session_id: str) -> RuntimeProviderInfoResponse:
    from app.services.runtime import get_runtime

    try:
        info = await get_runtime().describe(session_id)
        return RuntimeProviderInfoResponse(
            id=info.id,
            label=info.label,
            status=info.status,
            summary=info.summary,
            items=[
                RuntimeProviderInfoItemResponse(key=item.key, label=item.label, value=item.value)
                for item in info.items
            ],
        )
    except Exception:
        logger.warning("Could not describe runtime provider for session %s", session_id, exc_info=True)
        provider_id = (settings.runtime_exec_backend or "runtime").strip() or "runtime"
        label = {
            "docker": "Docker",
            "multipass": "Multipass",
            "qemu": "QEMU",
            "remote": "SSH",
        }.get(provider_id, provider_id.upper())
        return RuntimeProviderInfoResponse(
            id=provider_id,
            label=label,
            status="unknown",
            summary="Provider details unavailable.",
            items=[],
        )
