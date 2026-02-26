from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CreateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class SessionResponse(BaseModel):
    id: UUID
    user_id: str
    agent_id: str | None = None
    parent_session_id: UUID | None = None
    title: str | None = None
    initial_prompt: str | None = None
    latest_system_prompt: str | None = None
    status: str
    started_at: datetime
    ended_at: datetime | None = None


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    total: int


class SessionRuntimeActionResponse(BaseModel):
    timestamp: datetime | None = None
    action: str
    details: dict = Field(default_factory=dict)


class SessionRuntimeResponse(BaseModel):
    session_id: UUID
    runtime_exists: bool
    workspace_exists: bool
    venv_exists: bool
    active: bool
    active_pid: int | None = None
    last_command: str | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    last_active_at: datetime | None = None
    actions: list[SessionRuntimeActionResponse] = Field(default_factory=list)


class SessionRuntimeCleanupResponse(BaseModel):
    session_id: UUID
    runtime_removed: bool
    legacy_removed: bool


class CreateMessageRequest(BaseModel):
    role: Literal["user", "system"]
    content: str = Field(min_length=1, max_length=50_000)
    metadata: dict = Field(default_factory=dict)

    @field_validator("role", mode="before")
    @classmethod
    def _normalize_role(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("content must not be empty")
        return trimmed


class MessageResponse(BaseModel):
    id: UUID
    session_id: UUID
    role: str
    content: str
    metadata: dict = Field(default_factory=dict)
    token_count: int | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    created_at: datetime


class MessageListResponse(BaseModel):
    items: list[MessageResponse]
    has_more: bool


class ChatAttachment(BaseModel):
    mime_type: str = Field(min_length=1, max_length=64)
    base64: str = Field(min_length=1, max_length=8_000_000)
    filename: str | None = Field(default=None, max_length=200)


class ChatRequest(BaseModel):
    content: str = Field(default="", max_length=50_000)
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=4)
    model: str | None = None
    system_prompt: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_iterations: int = Field(default=10, ge=1, le=25)

    @field_validator("content")
    @classmethod
    def _validate_chat_content(cls, value: str) -> str:
        return value.strip()

    @field_validator("attachments")
    @classmethod
    def _validate_attachments(cls, value: list[ChatAttachment]) -> list[ChatAttachment]:
        if value:
            return value
        return []

    @field_validator("system_prompt")
    @classmethod
    def _normalize_system_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class ChatUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ChatResponse(BaseModel):
    response: str
    iterations: int
    usage: ChatUsage
    error: str | None = None
