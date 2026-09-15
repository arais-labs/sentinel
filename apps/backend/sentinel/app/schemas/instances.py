from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class InstanceAppearance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    icon: str = Field(default="initial", max_length=80, pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class InstanceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    display_name: str | None = Field(default=None, max_length=120)
    appearance: InstanceAppearance | None = None


class InstanceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=120)
    appearance: InstanceAppearance | None = None


class InstanceRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)


class InstanceResponse(BaseModel):
    name: str
    database_name: str
    display_name: str | None
    appearance: InstanceAppearance = Field(default_factory=InstanceAppearance)
    created_at: datetime | None = None
    updated_at: datetime | None = None
