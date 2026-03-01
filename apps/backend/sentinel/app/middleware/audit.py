from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def log_audit(
    db: AsyncSession,
    *,
    user_id: str | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request_summary: dict | None = None,
    status_code: int | None = None,
    ip_address: str | None = None,
    request_id: UUID | None = None,
    duration_ms: int | None = None,
) -> None:
    normalized_request_id = request_id
    if isinstance(request_id, str):
        try:
            normalized_request_id = UUID(request_id)
        except ValueError:
            normalized_request_id = None

    entry = AuditLog(
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
