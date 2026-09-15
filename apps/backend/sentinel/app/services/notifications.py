"""Shared publishing interface for the desktop inbox; no workspace or DB required."""

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings


class Notification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=4000)
    key: str | None = Field(default=None, min_length=1, max_length=200)
    severity: Literal["info", "success", "warning", "error", "urgent"] = "info"
    progress: bool = False
    target: dict[str, str] | None = None


async def publish_notification(notification: Notification) -> dict:
    if not settings.workspace_runtime_socket:
        raise ValueError("Notifications require the Sentinel desktop connection.")
    async with httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=settings.workspace_runtime_socket),
        base_url="http://sentinel-desktop",
        timeout=10,
        trust_env=False,
        headers={"x-sentinel-desktop-token": settings.sentinel_desktop_token},
    ) as client:
        try:
            response = await client.post(
                "/v1/notifications", json=notification.model_dump(exclude_none=True)
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError(
                "The notification could not be delivered to Sentinel. Try again."
            ) from exc
