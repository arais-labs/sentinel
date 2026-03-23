from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.config import settings
from app.middleware.auth import TokenPayload, require_auth
from app.schemas.runtime import (
    RuntimeResetResponse,
    RuntimeLiveViewResponse,
)
from app.services.runtime.runtime_live_view import (
    build_runtime_view_url,
    is_runtime_available_for_session,
)
from app.services.browser.pool import BrowserPool

router = APIRouter()


@router.get("/live-view", response_model=RuntimeLiveViewResponse)
async def get_live_view(
    request: Request,
    session_id: str = Query(..., description="Session UUID for per-session VNC"),
    _user: TokenPayload = Depends(require_auth),
) -> RuntimeLiveViewResponse:
    if not settings.runtime_live_view_enabled:
        return RuntimeLiveViewResponse(
            enabled=False,
            available=False,
            url=None,
            reason="Live desktop view is disabled.",
        )
    url = build_runtime_view_url(request, session_id=session_id)
    available = is_runtime_available_for_session(session_id)
    return RuntimeLiveViewResponse(
        enabled=True,
        available=available,
        url=url,
        reason=None if available else "Runtime desktop is not reachable.",
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
            manager = await pool.get(session_id)
            result = await manager.reset()
            return RuntimeResetResponse(**result)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to reset browser: {exc}") from exc

    from app.services.runtime import get_runtime

    # Fallback: kill and restart Chromium via SSH when no browser pool is available.
    try:
        provider = get_runtime()
        inst = provider._instances.get(str(session_id))
        if inst is None:
            raise HTTPException(status_code=404, detail="Runtime not found for session")
        # Kill existing Chromium, clean up singleton lock, then relaunch detached
        await inst.runtime.ssh.run(
            "pkill -f chromium-real || true; sleep 1; "
            "rm -f /home/sentinel/.config/chromium/SingletonLock "
            "/home/sentinel/.config/chromium/SingletonSocket "
            "/home/sentinel/.config/chromium/SingletonCookie 2>/dev/null || true"
        )
        await inst.runtime.ssh.run_detached(
            "bash -c 'DISPLAY=:99 chromium"
            " --no-sandbox --disable-gpu --disable-dev-shm-usage"
            " --remote-debugging-address=0.0.0.0 --remote-debugging-port=9222"
            " --disable-blink-features=AutomationControlled"
            " --no-first-run --no-default-browser-check"
            " --window-size=1920,1080 about:blank'",
            stdout_path="/tmp/chromium-reset.log",
            stderr_path="/tmp/chromium-reset.log",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to restart Chromium: {exc}") from exc

    return RuntimeResetResponse(reset=True, url="about:blank")


@router.post("/restart-container")
async def restart_container(
    request: Request,
    session_id: str = Query(..., description="Session UUID"),
    _user: TokenPayload = Depends(require_auth),
) -> dict:
    """Destroy and re-provision the runtime container for this session, keeping the session alive."""
    import asyncio
    from app.services.runtime import get_runtime
    from app.services.ws.ws_manager import ConnectionManager

    pool: BrowserPool | None = getattr(request.app.state, "browser_pool", None)
    if pool is not None:
        await pool.remove(session_id)

    try:
        provider = get_runtime()
        # Tear down the existing container
        await provider.destroy(session_id)
        # Re-provision in the background — broadcast runtime_ready when done
        ws: ConnectionManager | None = getattr(request.app.state, "ws_manager", None)
        asyncio.create_task(_provision_runtime_task(provider, session_id, ws))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to restart container: {exc}") from exc

    return {"restarting": True}


async def _provision_runtime_task(provider, session_id: str, ws) -> None:
    import logging
    logger = logging.getLogger(__name__)
    try:
        await provider.ensure(session_id)
        logger.info("Container restarted for session %s", session_id)
        if ws is not None and hasattr(ws, "broadcast_runtime_ready"):
            await ws.broadcast_runtime_ready(session_id)
    except Exception:
        logger.warning("Container restart failed for session %s", session_id, exc_info=True)


def _resolve_browser_pool(request: Request) -> BrowserPool | None:
    pool = getattr(request.app.state, "browser_pool", None)
    if isinstance(pool, BrowserPool):
        return pool
    return None
