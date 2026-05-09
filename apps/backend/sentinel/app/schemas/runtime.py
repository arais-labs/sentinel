from __future__ import annotations

from pydantic import BaseModel


class RuntimeProviderInfoItemResponse(BaseModel):
    key: str
    label: str
    value: str


class RuntimeProviderInfoResponse(BaseModel):
    id: str
    label: str
    status: str | None = None
    summary: str | None = None
    items: list[RuntimeProviderInfoItemResponse] = []


class RuntimeLiveViewResponse(BaseModel):
    enabled: bool
    available: bool
    mode: str = "novnc"
    url: str | None = None
    reason: str | None = None
    provider: RuntimeProviderInfoResponse


class RuntimeResetResponse(BaseModel):
    reset: bool
    url: str
    profile_dir: str | None = None
    stale_lock_cleared: bool = False
