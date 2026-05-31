from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class ApprovalRecord:
    provider: str
    approval_id: str
    status: str
    pending: bool
    label: str
    session_id: str | None = None
    command: str | None = None
    action: str | None = None
    description: str | None = None
    can_resolve: bool = False
    decision_note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ApprovalProviderError(RuntimeError):
    pass


class ApprovalNotFoundError(ApprovalProviderError):
    pass


class ApprovalConflictError(ApprovalProviderError):
    pass


class ApprovalProviderUnavailableError(ApprovalProviderError):
    pass
