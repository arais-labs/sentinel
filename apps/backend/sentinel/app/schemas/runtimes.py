from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


RuntimeProvider = Literal["ssh", "lima", "docker"]
RuntimeStatus = Literal["unknown", "creating", "stopped", "running", "ready", "error", "deleted"]
RuntimeJobStatus = Literal["queued", "running", "succeeded", "failed"]
RuntimeAuthType = Literal["private_key", "password"]
RuntimeAction = Literal["start", "stop", "rebuild", "delete"]


class RuntimeProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    desktop: str = "xfce"
    cpus: int | None = Field(default=None, ge=1)
    memory: str | None = None
    disk: str | None = None


class RuntimeProviderState(BaseModel):
    model_config = ConfigDict(extra="allow")

    ssh_config: str | None = None
    lima_name: str | None = None
    container_name: str | None = None
    workspace_volume: str | None = None
    desktop: str | None = None


class RuntimeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    provider: RuntimeProvider = "ssh"
    profile: str | None = Field(default=None, max_length=120)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=22, ge=1, le=65535)
    username: str | None = Field(default=None, min_length=1, max_length=120)
    workspaces_dir: str | None = Field(default=None, min_length=1)


class RuntimeCreateRequest(RuntimeBase):
    auth_type: RuntimeAuthType | None = None
    private_key: str | None = None
    password: str | None = None
    provider_config: RuntimeProviderConfig = Field(default_factory=RuntimeProviderConfig)

    @model_validator(mode="after")
    def validate_runtime(self) -> "RuntimeCreateRequest":
        if self.provider == "ssh":
            _validate_ssh_fields(
                host=self.host,
                username=self.username,
                workspaces_dir=self.workspaces_dir,
                auth_type=self.auth_type,
                private_key=self.private_key,
                password=self.password,
            )
        return self


class RuntimeUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    profile: str | None = Field(default=None, max_length=120)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, min_length=1, max_length=120)
    workspaces_dir: str | None = Field(default=None, min_length=1)
    auth_type: RuntimeAuthType | None = None
    private_key: str | None = None
    password: str | None = None
    provider_config: RuntimeProviderConfig | None = None

    @model_validator(mode="after")
    def validate_secret_update(self) -> "RuntimeUpdateRequest":
        if self.auth_type is not None or self.private_key is not None or self.password is not None:
            if self.auth_type is None:
                raise ValueError("auth_type is required when updating SSH auth.")
            _validate_secret(self.auth_type, self.private_key, self.password)
        return self


class RuntimeTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=120)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=120)
    workspaces_dir: str = Field(default="")
    auth_type: RuntimeAuthType
    private_key: str | None = None
    password: str | None = None

    @model_validator(mode="after")
    def validate_secret(self) -> "RuntimeTestRequest":
        _validate_secret(self.auth_type, self.private_key, self.password)
        return self


class RuntimeResponse(BaseModel):
    id: UUID
    name: str
    provider: RuntimeProvider
    status: RuntimeStatus
    profile: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    workspaces_dir: str | None = None
    auth_type: RuntimeAuthType | None = None
    provider_config: RuntimeProviderConfig = Field(default_factory=RuntimeProviderConfig)
    provider_state: RuntimeProviderState = Field(default_factory=RuntimeProviderState)
    last_job_id: UUID | None = None
    last_job_status: RuntimeJobStatus | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RuntimeTestResponse(BaseModel):
    ok: bool
    detail: str
    resolved_home: str | None = None
    resolved_workspaces_dir: str | None = None


class InstanceRuntimeUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_id: UUID | None = None


class RuntimeProviderCapability(BaseModel):
    provider: RuntimeProvider
    available: bool
    label: str
    detail: str
    missing: list[str] = Field(default_factory=list)


class RuntimeCapabilitiesResponse(BaseModel):
    providers: list[RuntimeProviderCapability]


class RuntimeJobEvent(BaseModel):
    timestamp: datetime
    level: Literal["info", "error"] = "info"
    message: str


class RuntimeJobResponse(BaseModel):
    id: UUID
    runtime_id: UUID | None
    provider: RuntimeProvider
    action: str
    status: RuntimeJobStatus
    events: list[RuntimeJobEvent] = Field(default_factory=list)
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class RuntimeActionResponse(BaseModel):
    runtime: RuntimeResponse
    job: RuntimeJobResponse


def _validate_ssh_fields(
    *,
    host: str | None,
    username: str | None,
    workspaces_dir: str | None,
    auth_type: RuntimeAuthType | None,
    private_key: str | None,
    password: str | None,
) -> None:
    if not (host or "").strip():
        raise ValueError("host is required for SSH runtimes.")
    if not (username or "").strip():
        raise ValueError("username is required for SSH runtimes.")
    if not (workspaces_dir or "").strip():
        raise ValueError("workspaces_dir is required for SSH runtimes.")
    if auth_type is None:
        raise ValueError("auth_type is required for SSH runtimes.")
    _validate_secret(auth_type, private_key, password)


def _validate_secret(auth_type: RuntimeAuthType, private_key: str | None, password: str | None) -> None:
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
