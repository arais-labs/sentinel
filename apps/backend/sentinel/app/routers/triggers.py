from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.audit import log_audit
from app.middleware.auth import TokenPayload, require_auth
from app.models import Trigger, TriggerLog
from app.schemas.triggers import (
    CreateTriggerRequest,
    FireTriggerRequest,
    TriggerListResponse,
    TriggerLogListResponse,
    TriggerLogResponse,
    TriggerResponse,
    UpdateTriggerRequest,
)
from app.services.trigger_scheduler import compute_next_fire_at

router = APIRouter()


@router.get("")
async def list_triggers(
    type: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> TriggerListResponse:
    _ = user
    result = await db.execute(select(Trigger))
    triggers = result.scalars().all()
    if type is not None:
        triggers = [t for t in triggers if t.type == type]
    if enabled is not None:
        triggers = [t for t in triggers if t.enabled == enabled]
    triggers.sort(key=lambda t: t.created_at, reverse=True)
    paged = triggers[offset : offset + limit]
    return TriggerListResponse(items=[_trigger_response(t) for t in paged], total=len(triggers))


@router.post("")
async def create_trigger(
    payload: CreateTriggerRequest,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    _ = user
    next_fire_at = None
    if payload.enabled:
        try:
            next_fire_at = compute_next_fire_at(payload.type, payload.config, reference_time=datetime.now(UTC))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))

    trigger = Trigger(
        name=payload.name,
        user_id=user.sub,
        type=payload.type,
        enabled=payload.enabled,
        config=payload.config,
        action_type=payload.action_type,
        action_config=payload.action_config,
        next_fire_at=next_fire_at,
        fire_count=0,
        error_count=0,
        consecutive_errors=0,
    )
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)
    return _trigger_response(trigger)


@router.get("/{id}")
async def get_trigger(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    _ = user
    trigger = await _get_trigger_or_404(db, id)
    return _trigger_response(trigger)


@router.patch("/{id}")
async def update_trigger(
    id: UUID,
    payload: UpdateTriggerRequest,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    _ = user
    trigger = await _get_trigger_or_404(db, id)

    if payload.name is not None:
        trigger.name = payload.name
    if payload.config is not None:
        trigger.config = payload.config
    if payload.action_config is not None:
        trigger.action_config = payload.action_config
    if payload.enabled is not None:
        trigger.enabled = payload.enabled
        if not payload.enabled:
            trigger.next_fire_at = None

    if trigger.enabled and (payload.config is not None or payload.enabled is True):
        try:
            trigger.next_fire_at = compute_next_fire_at(trigger.type, trigger.config, reference_time=datetime.now(UTC))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))

    await db.commit()
    return _trigger_response(trigger)


@router.delete("/{id}")
async def delete_trigger(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    _ = user
    trigger = await _get_trigger_or_404(db, id)
    await db.delete(trigger)
    await db.commit()
    return {"status": "deleted"}


@router.post("/{id}/fire")
async def fire_trigger(
    id: UUID,
    payload: FireTriggerRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> TriggerLogResponse:
    trigger = await _get_trigger_or_404(db, id)
    now = datetime.now(UTC)
    trigger.last_fired_at = now
    trigger.fire_count += 1
    trigger.consecutive_errors = 0
    trigger.last_error = None
    if trigger.enabled:
        try:
            trigger.next_fire_at = compute_next_fire_at(trigger.type, trigger.config, reference_time=now)
        except ValueError:
            trigger.next_fire_at = None

    log = TriggerLog(
        trigger_id=trigger.id,
        fired_at=now,
        status="fired",
        input_payload=payload.input_payload,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)

    await log_audit(
        db,
        user_id=user.sub,
        action="trigger.fire",
        resource_type="trigger",
        resource_id=str(trigger.id),
        status_code=200,
        ip_address=request.client.host if request.client else None,
        request_id=getattr(request.state, "request_id", None),
    )
    return _trigger_log_response(log)


@router.get("/{id}/logs")
async def list_trigger_logs(
    id: UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> TriggerLogListResponse:
    _ = user
    _ = await _get_trigger_or_404(db, id)
    result = await db.execute(select(TriggerLog).where(TriggerLog.trigger_id == id))
    logs = result.scalars().all()
    if status_filter is not None:
        logs = [item for item in logs if item.status == status_filter]
    logs.sort(key=lambda item: item.fired_at, reverse=True)
    paged = logs[offset : offset + limit]
    return TriggerLogListResponse(items=[_trigger_log_response(item) for item in paged], total=len(logs))


async def _get_trigger_or_404(db: AsyncSession, trigger_id: UUID) -> Trigger:
    result = await db.execute(select(Trigger).where(Trigger.id == trigger_id))
    trigger = result.scalars().first()
    if trigger is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trigger not found")
    return trigger


def _trigger_response(trigger: Trigger) -> TriggerResponse:
    return TriggerResponse(
        id=trigger.id,
        name=trigger.name,
        type=trigger.type,
        enabled=trigger.enabled,
        config=trigger.config,
        action_type=trigger.action_type,
        action_config=trigger.action_config,
        last_fired_at=trigger.last_fired_at,
        next_fire_at=trigger.next_fire_at,
        fire_count=trigger.fire_count,
        error_count=trigger.error_count,
        created_at=trigger.created_at,
    )


def _trigger_log_response(log: TriggerLog) -> TriggerLogResponse:
    return TriggerLogResponse(
        id=log.id,
        trigger_id=log.trigger_id,
        fired_at=log.fired_at,
        status=log.status,
        duration_ms=log.duration_ms,
        input_payload=log.input_payload,
        output_summary=log.output_summary,
        error_message=log.error_message,
    )
