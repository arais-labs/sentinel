from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _reject_control_chars(value: str | None) -> str | None:
    if value is not None and any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("name must not contain control characters or line breaks.")
    return value


MachineProvider = Literal["ssh", "local"]
MachineJobStatus = Literal["queued", "running", "succeeded", "failed"]
MachineAuthType = Literal["private_key", "password"]
MachineAction = Literal["start", "stop", "rebuild", "delete"]


class MachineStatus(StrEnum):
    UNKNOWN = "unknown"
    CREATING = "creating"
    STOPPED = "stopped"
    RUNNING = "running"
    READY = "ready"
    ERROR = "error"
    DELETED = "deleted"


class MachineProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host_key: str | None = None
    runtime_root: str | None = None
    runtime_version: str | None = None


class MachineProviderState(BaseModel):
    model_config = ConfigDict(extra="allow")


class MachineBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    provider: MachineProvider = "ssh"
    profile: str | None = Field(default=None, max_length=120)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=22, ge=1, le=65535)
    username: str | None = Field(default=None, min_length=1, max_length=120)

    _validate_name = field_validator("name")(_reject_control_chars)


class MachineCreateRequest(MachineBase):
    auth_type: MachineAuthType | None = None
    private_key: str | None = None
    password: str | None = None
    provider_config: MachineProviderConfig = Field(default_factory=MachineProviderConfig)

    @model_validator(mode="after")
    def validate_machine(self) -> "MachineCreateRequest":
        if self.provider == "ssh":
            _validate_ssh_fields(
                host=self.host,
                username=self.username,
                auth_type=self.auth_type,
                private_key=self.private_key,
                password=self.password,
            )
        return self


class MachineUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    profile: str | None = Field(default=None, max_length=120)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, min_length=1, max_length=120)
    auth_type: MachineAuthType | None = None
    private_key: str | None = None
    password: str | None = None
    provider_config: MachineProviderConfig | None = None

    _validate_name = field_validator("name")(_reject_control_chars)

    @model_validator(mode="after")
    def validate_secret_update(self) -> "MachineUpdateRequest":
        if self.auth_type is not None or self.private_key is not None or self.password is not None:
            if self.auth_type is None:
                raise ValueError("auth_type is required when updating SSH auth.")
            _validate_secret(self.auth_type, self.private_key, self.password)
        return self


class MachineTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=120)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=120)
    auth_type: MachineAuthType
    private_key: str | None = None
    password: str | None = None

    @model_validator(mode="after")
    def validate_secret(self) -> "MachineTestRequest":
        _validate_secret(self.auth_type, self.private_key, self.password)
        return self


class MachineResponse(BaseModel):
    id: UUID
    name: str
    provider: MachineProvider
    status: MachineStatus
    profile: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    auth_type: MachineAuthType | None = None
    provider_config: MachineProviderConfig = Field(default_factory=MachineProviderConfig)
    provider_state: MachineProviderState = Field(default_factory=MachineProviderState)
    status_detail: str | None = None
    last_job_id: UUID | None = None
    last_job_status: MachineJobStatus | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MachineTestResponse(BaseModel):
    ok: bool
    detail: str
    resolved_home: str | None = None


class MachineProviderCapability(BaseModel):
    provider: MachineProvider
    available: bool
    label: str
    detail: str
    missing: list[str] = Field(default_factory=list)
    # Whether the provider has real start/stop/rebuild actions (ssh + local don't).
    has_lifecycle: bool = True


class MachineCapabilitiesResponse(BaseModel):
    providers: list[MachineProviderCapability]


class MachineJobEvent(BaseModel):
    timestamp: datetime
    level: Literal["info", "error"] = "info"
    message: str


class MachineJobResponse(BaseModel):
    id: UUID
    machine_id: UUID | None
    provider: MachineProvider
    action: str
    status: MachineJobStatus
    events: list[MachineJobEvent] = Field(default_factory=list)
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class MachineLifecycleResponse(BaseModel):
    machine: MachineResponse
    job: MachineJobResponse


def _validate_ssh_fields(
    *,
    host: str | None,
    username: str | None,
    auth_type: MachineAuthType | None,
    private_key: str | None,
    password: str | None,
) -> None:
    if not (host or "").strip():
        raise ValueError("host is required for SSH machines.")
    if not (username or "").strip():
        raise ValueError("username is required for SSH machines.")
    if auth_type is None:
        raise ValueError("auth_type is required for SSH machines.")
    _validate_secret(auth_type, private_key, password)


def _validate_secret(
    auth_type: MachineAuthType, private_key: str | None, password: str | None
) -> None:
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
