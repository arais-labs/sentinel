from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.models.manager import ManagerAuditLog


async def _write_audit(
    db: AsyncSession,
    model: type,
    *,
    user_id: str | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request_summary: dict | None = None,
    status_code: int | None = None,
    ip_address: str | None = None,
    request_id: UUID | str | None = None,
    duration_ms: int | None = None,
) -> None:
    normalized_request_id: UUID | None
    if isinstance(request_id, str):
        try:
            normalized_request_id = UUID(request_id)
        except ValueError:
            normalized_request_id = None
    else:
        normalized_request_id = request_id

    entry = model(
        timestamp=datetime.now(UTC),
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_summary=request_summary,
        status_code=status_code,
        ip_address=ip_address,
        request_id=normalized_request_id,
        duration_ms=duration_ms,
    )
    db.add(entry)
    await db.commit()


async def log_audit(db: AsyncSession, **fields) -> None:
    """Record a per-instance audit event (writes to the instance DB)."""
    await _write_audit(db, AuditLog, **fields)


async def log_manager_audit(db: AsyncSession, **fields) -> None:
    """Record a manager-scoped audit event (writes to the manager DB)."""
    await _write_audit(db, ManagerAuditLog, **fields)
