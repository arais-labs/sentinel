from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.audit import log_audit
from app.middleware.auth import TokenPayload, require_auth
from app.models import Trigger, TriggerLog
from app.schemas.triggers import (
    CreateTriggerRequest,
    FireTriggerResponse,
    FireTriggerRequest,
    TriggerListResponse,
    TriggerLogListResponse,
    TriggerLogResponse,
    TriggerResponse,
    UpdateTriggerRequest,
)
from app.services.trigger_scheduler import TriggerScheduler, compute_next_fire_at
from app.services.triggers.routing import resolve_agent_message_route

logger = logging.getLogger(__name__)
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
    stmt = select(Trigger).where(Trigger.user_id == user.sub)
    if type is not None:
        stmt = stmt.where(Trigger.type == type)
    if enabled is not None:
        stmt = stmt.where(Trigger.enabled == enabled)
    stmt = stmt.order_by(Trigger.created_at.desc())

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    paged = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return TriggerListResponse(items=[_trigger_response(t) for t in paged], total=total)


@router.post("")
async def create_trigger(
    payload: CreateTriggerRequest,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    next_fire_at = None
    if payload.enabled:
        try:
            next_fire_at = compute_next_fire_at(payload.type, payload.config, reference_time=datetime.now(UTC))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    action_config = payload.action_config
    if payload.action_type == "agent_message":
        action_config = await _normalize_agent_message_action_config(
            db,
            user_id=user.sub,
            action_config=payload.action_config,
        )

    trigger = Trigger(
        name=payload.name,
        user_id=user.sub,
        type=payload.type,
        enabled=payload.enabled,
        config=payload.config,
        action_type=payload.action_type,
        action_config=action_config,
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
    trigger = await _get_trigger_or_404(db, id, user.sub)
    return _trigger_response(trigger)


@router.patch("/{id}")
async def update_trigger(
    id: UUID,
    payload: UpdateTriggerRequest,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    trigger = await _get_trigger_or_404(db, id, user.sub)

    if payload.name is not None:
        trigger.name = payload.name
    if payload.type is not None:
        trigger.type = payload.type
    if payload.config is not None:
        trigger.config = payload.config

    new_action_type = payload.action_type if payload.action_type is not None else trigger.action_type
    if payload.action_config is not None or payload.action_type is not None:
        if new_action_type == "agent_message":
            trigger.action_config = await _normalize_agent_message_action_config(
                db,
                user_id=user.sub,
                action_config=payload.action_config if payload.action_config is not None else trigger.action_config,
            )
        else:
            trigger.action_config = payload.action_config if payload.action_config is not None else trigger.action_config
        
        if payload.action_type is not None:
            trigger.action_type = payload.action_type

    if payload.enabled is not None:
        trigger.enabled = payload.enabled
        if not payload.enabled:
            trigger.next_fire_at = None

    if trigger.enabled and (payload.config is not None or payload.type is not None or payload.enabled is True):
        try:
            trigger.next_fire_at = compute_next_fire_at(trigger.type, trigger.config, reference_time=datetime.now(UTC))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    await db.commit()
    return _trigger_response(trigger)


@router.delete("/{id}")
async def delete_trigger(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    trigger = await _get_trigger_or_404(db, id, user.sub)
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
) -> FireTriggerResponse:
    trigger = await _get_trigger_or_404(db, id, user.sub)
    scheduler: TriggerScheduler | None = getattr(request.app.state, "trigger_scheduler", None)
    if scheduler is None:
        scheduler = TriggerScheduler(
            agent_loop=getattr(request.app.state, "agent_loop", None),
            tool_executor=getattr(request.app.state, "tool_executor", None),
            ws_manager=getattr(request.app.state, "ws_manager", None),
            db_factory=None,
        )
    outcome = await scheduler.fire_now_nonblocking(
        db,
        trigger_id=trigger.id,
        input_payload=payload.input_payload,
        force=True,
    )
    if outcome is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trigger could not be invoked",
        )
    log = outcome.log
    action = outcome.action
    if (
        trigger.action_type == "agent_message"
        and log.status == "fired"
        and action.resolved_session_id is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trigger fired but did not resolve a target session",
        )

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
    return FireTriggerResponse(
        log=_trigger_log_response(log),
        resolved_session_id=action.resolved_session_id,
        route_mode=action.route_mode,
        used_fallback=action.used_fallback,
    )


@router.get("/{id}/logs")
async def list_trigger_logs(
    id: UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> TriggerLogListResponse:
    await _get_trigger_or_404(db, id, user.sub)

    stmt = select(TriggerLog).where(TriggerLog.trigger_id == id)
    if status_filter is not None:
        stmt = stmt.where(TriggerLog.status == status_filter)
    stmt = stmt.order_by(TriggerLog.fired_at.desc())

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    paged = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return TriggerLogListResponse(items=[_trigger_log_response(item) for item in paged], total=total)


async def _get_trigger_or_404(db: AsyncSession, trigger_id: UUID, user_id: str) -> Trigger:
    result = await db.execute(
        select(Trigger).where(Trigger.id == trigger_id, Trigger.user_id == user_id)
    )
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


async def _normalize_agent_message_action_config(
    db: AsyncSession,
    *,
    user_id: str,
    action_config: dict,
) -> dict:
    if not isinstance(action_config, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="agent_message action_config must be an object",
        )
    message = action_config.get("message")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="agent_message action requires non-empty action_config.message",
        )
    route = await resolve_agent_message_route(
        db,
        user_id=user_id,
        action_config=action_config,
    )
    return route.normalized_action_config
