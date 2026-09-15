from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CreateSubAgentTaskRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scope: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    tier: str | None = Field(default=None, pattern="^(fast|normal|hard)$")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name must not be empty")
        return trimmed

    @field_validator("scope")
    @classmethod
    def _normalize_scope(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class SubAgentTaskResponse(BaseModel):
    id: UUID
    session_id: UUID
    name: str
    scope: str | None = None
    status: str
    allowed_tools: list[str] = Field(default_factory=list)
    turns_used: int = 0
    tokens_used: int = 0
    model: str | None = None
    usage: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SubAgentTaskListResponse(BaseModel):
    items: list[SubAgentTaskResponse]
    total: int


class InterjectRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
