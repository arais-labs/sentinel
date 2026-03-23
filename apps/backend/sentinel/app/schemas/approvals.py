from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ApprovalRecordResponse(BaseModel):
    provider: str
    approval_id: str
    status: str
    pending: bool
    label: str
    session_id: UUID | None = None
    command: str | None = None
    action: str | None = None
    description: str | None = None
    can_resolve: bool
    decision_note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalListResponse(BaseModel):
    items: list[ApprovalRecordResponse]
    total: int

class ResolveApprovalRequest(BaseModel):
    note: str | None = None
