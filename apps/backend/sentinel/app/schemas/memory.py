from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class StoreMemoryRequest(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=10_000)
    category: Literal["core", "preference", "project", "correction"]
    parent_id: UUID | None = None
    importance: int = Field(default=0, ge=0, le=100)
    pinned: bool = False
    metadata: dict = Field(default_factory=dict)
    embedding: list[float] | None = None

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("content must not be empty")
        return trimmed

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class UpdateMemoryRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=50_000)
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=10_000)
    category: Literal["core", "preference", "project", "correction"] | None = None
    parent_id: UUID | None = None
    importance: int | None = Field(default=None, ge=0, le=100)
    pinned: bool | None = None
    metadata: dict | None = None

    @field_validator("content")
    @classmethod
    def _validate_optional_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("content must not be empty")
        return trimmed

    @field_validator("title")
    @classmethod
    def _validate_optional_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @field_validator("summary")
    @classmethod
    def _validate_optional_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class MemoryResponse(BaseModel):
    id: UUID
    content: str
    title: str | None = None
    summary: str | None = None
    category: str
    parent_id: UUID | None = None
    importance: int = 0
    pinned: bool = False
    is_system: bool = False
    system_key: str | None = None
    metadata: dict = Field(default_factory=dict)
    session_id: UUID | None = None
    score: float | None = None
    last_accessed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MemoryListResponse(BaseModel):
    items: list[MemoryResponse]
    total: int


class MemoryStatsResponse(BaseModel):
    total_memories: int
    categories: dict[str, int]


class MemoryChildrenResponse(BaseModel):
    parent_id: UUID
    items: list[MemoryResponse]
    total: int


class MemorySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    category: Literal["core", "preference", "project", "correction"] | None = None
    root_id: UUID | None = None
    limit: int = Field(default=20, ge=1, le=200)


class MemoryBackupNode(BaseModel):
    external_id: str = Field(min_length=1, max_length=200)
    parent_external_id: str | None = Field(default=None, max_length=200)
    content: str = Field(min_length=1, max_length=50_000)
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=10_000)
    category: Literal["core", "preference", "project", "correction"]
    importance: int = Field(default=0, ge=0, le=100)
    pinned: bool = False
    is_system: bool = False
    system_key: str | None = Field(default=None, max_length=100)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MemoryBackupDocument(BaseModel):
    schema_version: Literal["memory_backup_v1"] = "memory_backup_v1"
    exported_at: datetime
    nodes: list[MemoryBackupNode] = Field(default_factory=list)


class MemoryBackupImportRequest(BaseModel):
    document: MemoryBackupDocument
    mode: Literal["merge", "replace_non_system", "replace_all"] = "merge"


class MemoryBackupImportResponse(BaseModel):
    total_in_backup: int
    created: int
    updated: int
    deleted: int
    skipped: int
