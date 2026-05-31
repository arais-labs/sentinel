"""Manager-scoped admin endpoints (no instance_name in path)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_manager_db
from app.middleware.auth import TokenPayload, require_admin
from app.models.manager import ManagerAuditLog
from app.schemas.admin import AuditLogListResponse, AuditLogResponse

router = APIRouter()


@router.get("/audit")
async def list_manager_audit_logs(
    action: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
) -> AuditLogListResponse:
    stmt = select(ManagerAuditLog).order_by(ManagerAuditLog.timestamp.desc())
    if action:
        stmt = stmt.where(ManagerAuditLog.action == action)
    if user_id:
        stmt = stmt.where(ManagerAuditLog.user_id == user_id)
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    paged = rows[offset : offset + limit]
    return AuditLogListResponse(
        items=[
            AuditLogResponse(
                id=item.id,
                timestamp=item.timestamp or datetime.now(UTC),
                user_id=item.user_id,
                action=item.action,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                status_code=item.status_code,
                ip_address=str(item.ip_address) if item.ip_address is not None else None,
                request_id=item.request_id,
            )
            for item in paged
        ],
        total=len(rows),
    )
