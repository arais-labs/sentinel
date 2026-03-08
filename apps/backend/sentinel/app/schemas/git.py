from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class GitAccountResponse(BaseModel):
    id: UUID
    name: str
    host: str
    scope_pattern: str
    author_name: str
    author_email: str
    has_read_token: bool
    has_write_token: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GitAccountListResponse(BaseModel):
    items: list[GitAccountResponse]
    total: int


class CreateGitAccountRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=255)
    scope_pattern: str = Field(default="*", min_length=1, max_length=500)
    author_name: str = Field(min_length=1, max_length=255)
    author_email: str = Field(min_length=3, max_length=320)
    token_read: str = Field(min_length=1)
    token_write: str = Field(min_length=1)


class UpdateGitAccountRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    scope_pattern: str | None = Field(default=None, min_length=1, max_length=500)
    author_name: str | None = Field(default=None, min_length=1, max_length=255)
    author_email: str | None = Field(default=None, min_length=3, max_length=320)
    token_read: str | None = Field(default=None, min_length=1)
    token_write: str | None = Field(default=None, min_length=1)
