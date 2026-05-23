from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


RuntimeTargetAuthType = Literal["private_key", "password"]


class RuntimeSSHTargetBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=120)
    workspaces_dir: str = Field(min_length=1)


class RuntimeSSHTargetCreateRequest(RuntimeSSHTargetBase):
    auth_type: RuntimeTargetAuthType
    private_key: str | None = None
    password: str | None = None

    @model_validator(mode="after")
    def validate_secret(self) -> "RuntimeSSHTargetCreateRequest":
        _validate_secret(self.auth_type, self.private_key, self.password)
        return self


class RuntimeSSHTargetUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, min_length=1, max_length=120)
    workspaces_dir: str | None = Field(default=None, min_length=1)
    auth_type: RuntimeTargetAuthType | None = None
    private_key: str | None = None
    password: str | None = None

    @model_validator(mode="after")
    def validate_secret(self) -> "RuntimeSSHTargetUpdateRequest":
        if self.auth_type is not None or self.private_key is not None or self.password is not None:
            if self.auth_type is None:
                raise ValueError("auth_type is required when updating SSH auth.")
            _validate_secret(self.auth_type, self.private_key, self.password)
        return self


class RuntimeSSHTargetTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=120)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=120)
    workspaces_dir: str = Field(default="")
    auth_type: RuntimeTargetAuthType
    private_key: str | None = None
    password: str | None = None

    @model_validator(mode="after")
    def validate_secret(self) -> "RuntimeSSHTargetTestRequest":
        _validate_secret(self.auth_type, self.private_key, self.password)
        return self


class RuntimeSSHTargetResponse(RuntimeSSHTargetBase):
    id: UUID
    auth_type: RuntimeTargetAuthType
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RuntimeSSHTargetTestResponse(BaseModel):
    ok: bool
    detail: str
    resolved_home: str | None = None
    resolved_workspaces_dir: str | None = None


class InstanceRuntimeTargetUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_target_id: UUID | None = None


def _validate_secret(auth_type: RuntimeTargetAuthType, private_key: str | None, password: str | None) -> None:
    key = (private_key or "").strip()
    pw = password or ""
    if auth_type == "private_key":
        if not key:
            raise ValueError("private_key is required for private_key auth.")
        if pw:
            raise ValueError("password must be empty for private_key auth.")
    if auth_type == "password":
        if not pw:
            raise ValueError("password is required for password auth.")
        if key:
            raise ValueError("private_key must be empty for password auth.")
