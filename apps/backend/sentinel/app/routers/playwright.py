from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.config import settings
from app.middleware.auth import TokenPayload, require_auth
from app.schemas.playwright import (
    PlaywrightBrowserResetResponse,
    PlaywrightLiveViewResponse,
)
from app.services.browser_live_view import build_live_view_url, is_live_view_available
from app.services.tools.browser_tool import BrowserManager

router = APIRouter()


@router.get("/live-view", response_model=PlaywrightLiveViewResponse)
async def get_live_view(
    request: Request,
    _user: TokenPayload = Depends(require_auth),
) -> PlaywrightLiveViewResponse:
    if not settings.browser_live_view_enabled:
        return PlaywrightLiveViewResponse(
            enabled=False,
            available=False,
            url=None,
            reason="Live browser view is disabled.",
        )
    url = build_live_view_url(request)
    available = is_live_view_available()
    return PlaywrightLiveViewResponse(
        enabled=True,
        available=available,
        url=url,
        reason=None if available else "Live browser runtime is not reachable.",
    )


@router.post("/reset-browser", response_model=PlaywrightBrowserResetResponse)
async def reset_browser_runtime(
    request: Request,
    _user: TokenPayload = Depends(require_auth),
) -> PlaywrightBrowserResetResponse:
    manager = _resolve_browser_manager(request)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Browser manager is not available",
        )
    payload = await manager.reset()
    return PlaywrightBrowserResetResponse(**payload)


def _resolve_browser_manager(request: Request) -> BrowserManager | None:
    manager = getattr(request.app.state, "browser_manager", None)
    if isinstance(manager, BrowserManager):
        return manager
    return None
