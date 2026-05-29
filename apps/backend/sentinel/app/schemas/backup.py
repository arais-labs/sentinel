from __future__ import annotations

from pydantic import BaseModel, Field


class ItemInfo(BaseModel):
    key: str
    label: str


class ItemsResponse(BaseModel):
    items: list[ItemInfo]


class ExportRequest(BaseModel):
    items: list[str] = Field(default_factory=list)
    passphrase: str


class InspectRequest(BaseModel):
    data: str  # base64-encoded backup blob
    passphrase: str


class BackupInfoResponse(BaseModel):
    source_instance: str | None = None
    created_at: str | None = None
    created_by_version: str | None = None
    items: list[str] = Field(default_factory=list)
    restorable: bool = True
    compatibility: str | None = None


class ImportRequest(BaseModel):
    data: str  # base64-encoded backup blob
    passphrase: str
    items: list[str] | None = None


class ImportResponse(BaseModel):
    imported: int
    skipped: int
    by_table: dict[str, dict[str, int]] = Field(default_factory=dict)
