from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GitAccountResponse(BaseModel):
    id: UUID
    name: str
    host: str
    scope_pattern: str
    author_name: str
    author_email: str
    has_token: bool
    github_login: str | None = None
    verified_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GitAccountListResponse(BaseModel):
    items: list[GitAccountResponse]
    total: int


class CreateGitAccountRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    token: str = Field(min_length=1, max_length=4096, repr=False)
    host: Literal["github.com"] = "github.com"
    name: str | None = Field(default=None, min_length=1, max_length=120)
    scope_pattern: str = Field(default="*", min_length=1, max_length=500)
    author_name: str | None = Field(default=None, min_length=1, max_length=255)
    author_email: str | None = Field(default=None, min_length=3, max_length=320)


class UpdateGitAccountRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    scope_pattern: str | None = Field(default=None, min_length=1, max_length=500)
    author_name: str | None = Field(default=None, min_length=1, max_length=255)
    author_email: str | None = Field(default=None, min_length=3, max_length=320)
    token: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)
