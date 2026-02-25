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
    title: str | None = None
    status: str
    started_at: datetime
    ended_at: datetime | None = None


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    total: int


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


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    model: str | None = None
    system_prompt: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_iterations: int = Field(default=10, ge=1, le=25)

    @field_validator("content")
    @classmethod
    def _validate_chat_content(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("content must not be empty")
        return trimmed

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
