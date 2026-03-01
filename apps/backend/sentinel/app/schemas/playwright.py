from __future__ import annotations

from pydantic import BaseModel


class PlaywrightLiveViewResponse(BaseModel):
    enabled: bool
    available: bool
    mode: str = "novnc"
    url: str | None = None
    reason: str | None = None


class PlaywrightBrowserResetResponse(BaseModel):
    reset: bool
    url: str
    profile_dir: str | None = None
    stale_lock_cleared: bool = False
