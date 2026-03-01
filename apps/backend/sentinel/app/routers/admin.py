from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.middleware.audit import log_audit
from app.middleware.auth import TokenPayload, require_admin
from app.models import AuditLog
from app.schemas.admin import AuditLogListResponse, AuditLogResponse, ConfigResponse, UpdateConfigRequest
from app.services.estop import EstopLevel, EstopService

router = APIRouter()
_estop = EstopService()


@router.post("/estop")
async def emergency_stop(
    request: Request,
    level: int = Query(default=int(EstopLevel.TOOL_FREEZE), ge=0, le=3),
    user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    resolved_level = EstopLevel.coerce(level)
    await _estop.set_level(db, resolved_level)
    await log_audit(
        db,
        user_id=user.sub,
        action="admin.estop",
        resource_type="system",
        resource_id="estop",
        status_code=200,
        ip_address=request.client.host if request.client else None,
        request_id=getattr(request.state, "request_id", None),
    )
    return {
        "status": "activated" if resolved_level != EstopLevel.NONE else "deactivated",
        "message": "Emergency stop updated",
        "level": int(resolved_level),
    }


@router.get("/estop")
async def get_emergency_stop(
    user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int | bool]:
    _ = user
    level = await _estop.check_level(db)
    return {"level": int(level), "active": level != EstopLevel.NONE}


@router.delete("/estop")
async def clear_emergency_stop(
    request: Request,
    user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    await _estop.set_level(db, EstopLevel.NONE)
    await log_audit(
        db,
        user_id=user.sub,
        action="admin.estop.clear",
        resource_type="system",
        resource_id="estop",
        status_code=200,
        ip_address=request.client.host if request.client else None,
        request_id=getattr(request.state, "request_id", None),
    )
    return {"status": "deactivated", "message": "Emergency stop cleared", "level": int(EstopLevel.NONE)}


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
    db: AsyncSession = Depends(get_db),
) -> ConfigResponse:
    _ = user
    return ConfigResponse(
        app_name=settings.app_name,
        app_env=settings.app_env,
        estop_active=await _estop.is_active(db),
        jwt_algorithm=settings.jwt_algorithm,
        access_token_ttl_seconds=settings.access_token_ttl_seconds,
        refresh_token_ttl_seconds=settings.refresh_token_ttl_seconds,
        context_token_budget=settings.context_token_budget,
        araios_url=settings.araios_url,
        jwt_secret_key="***",
        dev_token="***",
    )


@router.patch("/config")
async def update_config(
    payload: UpdateConfigRequest,
    user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ConfigResponse:
    _ = user
    if payload.access_token_ttl_seconds is not None:
        settings.access_token_ttl_seconds = payload.access_token_ttl_seconds
    if payload.refresh_token_ttl_seconds is not None:
        settings.refresh_token_ttl_seconds = payload.refresh_token_ttl_seconds
    if payload.context_token_budget is not None:
        settings.context_token_budget = payload.context_token_budget
    return ConfigResponse(
        app_name=settings.app_name,
        app_env=settings.app_env,
        estop_active=await _estop.is_active(db),
        jwt_algorithm=settings.jwt_algorithm,
        access_token_ttl_seconds=settings.access_token_ttl_seconds,
        refresh_token_ttl_seconds=settings.refresh_token_ttl_seconds,
        context_token_budget=settings.context_token_budget,
        araios_url=settings.araios_url,
        jwt_secret_key="***",
        dev_token="***",
    )
