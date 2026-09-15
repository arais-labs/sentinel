from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models import AuditLog
from app.schemas.admin import AuditLogListResponse, AuditLogResponse, ConfigResponse

router = APIRouter()


@router.get("/audit")
async def list_audit_logs(
    action: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AuditLogListResponse:
    filters = []
    if action:
        filters.append(AuditLog.action == action)
    if user_id:
        filters.append(AuditLog.user_id == user_id)
    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*filters))
    result = await db.execute(
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
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
            for item in result.scalars()
        ],
        total=total or 0,
    )


@router.get("/config")
async def get_config() -> ConfigResponse:
    return ConfigResponse(
        app_name=settings.app_name,
        app_env=settings.app_env,
        context_token_budget=settings.context_token_budget,
    )
