from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


RuntimeBackend = Literal["docker", "qemu", "remote"]


class InstanceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    display_name: str | None = Field(default=None, max_length=120)
    runtime_backend: RuntimeBackend = "docker"
    runtime_config: dict[str, Any] = Field(default_factory=dict)


class InstanceUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    runtime_backend: RuntimeBackend | None = None
    runtime_config: dict[str, Any] | None = None


class InstanceRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class InstanceResponse(BaseModel):
    name: str
    database_name: str
    display_name: str | None
    runtime_backend: str
    runtime_config: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None
