from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CreatePlaywrightTaskRequest(BaseModel):
    url: str = Field(min_length=1)
    action: Literal["screenshot", "extract", "interact"] = "screenshot"
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        trimmed = value.strip()
        if not (trimmed.startswith("http://") or trimmed.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return trimmed


class PlaywrightTaskResponse(BaseModel):
    id: UUID
    user_id: str
    url: str
    action: str
    status: str
    result: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class PlaywrightTaskListResponse(BaseModel):
    items: list[PlaywrightTaskResponse]
    total: int


class PlaywrightScreenshotResponse(BaseModel):
    screenshot_base64: str
    page_title: str
    url: str


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
