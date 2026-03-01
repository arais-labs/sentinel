from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Union, Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# --- Trigger Configurations ---

class CronTriggerConfig(BaseModel):
    expr: str = Field(description="Cron expression (e.g. '0 9 * * *')")

class HeartbeatTriggerConfig(BaseModel):
    interval_seconds: int = Field(gt=0, description="Interval between fires in seconds")

class WebhookTriggerConfig(BaseModel):
    # Future: add secret_header, allowed_ips, etc.
    pass

class EventTriggerConfig(BaseModel):
    event_name: str | None = None

TriggerConfig = Annotated[
    Union[CronTriggerConfig, HeartbeatTriggerConfig, WebhookTriggerConfig, EventTriggerConfig],
    Field(discriminator="type_hint") # Internal helper for parsing if needed, or we map manually
]

# --- Action Configurations ---

class AgentMessageActionConfig(BaseModel):
    message: str = Field(min_length=1)
    route_mode: Literal["main", "session"] = "main"
    target_session_id: UUID | None = None
    # Backward compatibility (legacy + resolved route target)
    session_id: UUID | None = None
    resolved_session_id: UUID | None = None

class ToolCallActionConfig(BaseModel):
    name: str = Field(min_length=1, description="Name of the tool to execute")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Arguments for the tool")

class HttpRequestActionConfig(BaseModel):
    url: str = Field(min_length=1)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any | None = None
    timeout_seconds: int = Field(default=10, gt=0)

# --- Requests / Responses ---

class CreateTriggerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    user_id: str | None = Field(default=None, max_length=100)
    type: Literal["cron", "webhook", "heartbeat", "event"]
    config: dict[str, Any] = Field(default_factory=dict, description="Configuration for the trigger entry point")
    action_type: Literal["agent_message", "tool_call", "http_request"]
    action_config: dict[str, Any] = Field(default_factory=dict, description="Configuration for the execution action")
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name must not be empty")
        return trimmed

class UpdateTriggerRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    type: Literal["cron", "webhook", "heartbeat", "event"] | None = None
    config: dict[str, Any] | None = None
    action_type: Literal["agent_message", "tool_call", "http_request"] | None = None
    action_config: dict[str, Any] | None = None
    enabled: bool | None = None

class TriggerResponse(BaseModel):
    id: UUID
    name: str
    type: str
    enabled: bool
    config: dict[str, Any]
    action_type: str
    action_config: dict[str, Any]
    last_fired_at: datetime | None = None
    next_fire_at: datetime | None = None
    fire_count: int
    error_count: int
    created_at: datetime

class TriggerListResponse(BaseModel):
    items: list[TriggerResponse]
    total: int

class TriggerLogResponse(BaseModel):
    id: UUID
    trigger_id: UUID
    fired_at: datetime
    status: str
    duration_ms: int | None = None
    input_payload: dict[str, Any] | None = None
    output_summary: str | None = None
    error_message: str | None = None

class TriggerLogListResponse(BaseModel):
    items: list[TriggerLogResponse]
    total: int

class FireTriggerRequest(BaseModel):
    input_payload: dict[str, Any] = Field(default_factory=dict)
