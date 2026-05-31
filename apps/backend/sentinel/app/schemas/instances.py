from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InstanceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    display_name: str | None = Field(default=None, max_length=120)


class InstanceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=120)


class InstanceRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)


class InstanceResponse(BaseModel):
    name: str
    database_name: str
    display_name: str | None
    runtime_id: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
