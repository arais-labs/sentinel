from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_admin
from app.models import AuditLog
from app.schemas.admin import AuditLogListResponse, AuditLogResponse, ConfigResponse

router = APIRouter()


@router.get("/audit")
async def list_audit_logs(
    action: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AuditLogListResponse:
    _ = user
    result = await db.execute(select(AuditLog))
    items = result.scalars().all()
    if action:
        items = [item for item in items if item.action == action]
    if user_id:
        items = [item for item in items if item.user_id == user_id]
    items.sort(key=lambda item: item.timestamp or datetime.min.replace(tzinfo=UTC), reverse=True)
    paged = items[offset : offset + limit]
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
        total=len(items),
    )


@router.get("/config")
async def get_config(
    user: TokenPayload = Depends(require_admin),
) -> ConfigResponse:
    _ = user
    return ConfigResponse(
        app_name=settings.app_name,
        app_env=settings.app_env,
        jwt_algorithm=settings.jwt_algorithm,
        access_token_ttl_seconds=settings.access_token_ttl_seconds,
        refresh_token_ttl_seconds=settings.refresh_token_ttl_seconds,
        context_token_budget=settings.context_token_budget,
        jwt_secret_key="***",
    )
