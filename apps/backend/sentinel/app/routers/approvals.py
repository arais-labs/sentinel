from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import SessionActionGrant
from app.schemas.approvals import (
    ApprovalListResponse,
    ApprovalRecordResponse,
    ResolveApprovalRequest,
    SessionActionGrantResponse,
)
from app.services.tools.approval import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalProviderUnavailableError,
    ApprovalService,
)
from app.services.tools.approval.types import ApprovalRecord

router = APIRouter()


@router.get("/sessions/{session_id}/grants")
async def list_session_grants(
    session_id: UUID, db: AsyncSession = Depends(get_db)
) -> list[SessionActionGrantResponse]:
    rows = await db.scalars(
        select(SessionActionGrant)
        .where(
            SessionActionGrant.session_id == session_id,
        )
        .order_by(SessionActionGrant.action)
    )
    return [SessionActionGrantResponse.model_validate(row, from_attributes=True) for row in rows]


@router.delete("/sessions/{session_id}/grants/{grant_id}", status_code=204)
async def revoke_session_grant(
    session_id: UUID, grant_id: UUID, db: AsyncSession = Depends(get_db)
) -> None:
    await db.execute(
        delete(SessionActionGrant).where(
            SessionActionGrant.id == grant_id,
            SessionActionGrant.session_id == session_id,
        )
    )
    await db.commit()


@router.get("")
async def list_approvals(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    provider: str | None = Query(default=None),
    session_id: UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> ApprovalListResponse:
    service = _resolve_approval_service(request)
    try:
        items, total = await service.list_approvals(
            db,
            status_filter=status_filter,
            provider=provider,
            session_id=session_id,
            limit=limit,
            offset=offset,
        )
    except ApprovalProviderUnavailableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ApprovalListResponse(items=[_record_response(item) for item in items], total=total)


@router.post("/{provider}/{approval_id}/approve")
async def approve_approval(
    request: Request,
    provider: str,
    approval_id: str,
    payload: ResolveApprovalRequest,
    db: AsyncSession = Depends(get_db),
) -> ApprovalRecordResponse:
    return await _resolve(
        request=request,
        provider=provider,
        approval_id=approval_id,
        decision="approve",
        note=payload.note,
        scope=payload.scope,
        decision_by="local",
        db=db,
    )


@router.post("/{provider}/{approval_id}/reject")
async def reject_approval(
    request: Request,
    provider: str,
    approval_id: str,
    payload: ResolveApprovalRequest,
    db: AsyncSession = Depends(get_db),
) -> ApprovalRecordResponse:
    return await _resolve(
        request=request,
        provider=provider,
        approval_id=approval_id,
        decision="reject",
        note=payload.note,
        scope=payload.scope,
        decision_by="local",
        db=db,
    )


async def _resolve(
    *,
    request: Request,
    provider: str,
    approval_id: str,
    decision: Literal["approve", "reject"],
    note: str | None,
    scope: str,
    decision_by: str,
    db: AsyncSession,
) -> ApprovalRecordResponse:
    service = _resolve_approval_service(request)
    try:
        record = await service.resolve_approval(
            db,
            provider=provider,
            approval_id=approval_id,
            decision=decision,
            decision_by=decision_by,
            note=note,
            scope=scope,
        )
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApprovalProviderUnavailableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _record_response(record)


def _resolve_approval_service(request: Request) -> ApprovalService:
    service = getattr(request.app.state, "approval_service", None)
    if isinstance(service, ApprovalService):
        return service
    raise HTTPException(status_code=500, detail="Approval service is not initialized")


def _record_response(record: ApprovalRecord) -> ApprovalRecordResponse:
    session_uuid: UUID | None = None
    if isinstance(record.session_id, str) and record.session_id.strip():
        try:
            session_uuid = UUID(record.session_id)
        except ValueError:
            session_uuid = None
    return ApprovalRecordResponse(
        provider=record.provider,
        approval_id=record.approval_id,
        status=record.status,
        pending=record.pending,
        label=record.label,
        session_id=session_uuid,
        command=record.command,
        action=record.action,
        description=record.description,
        can_resolve=record.can_resolve,
        decision_note=record.decision_note,
        created_at=record.created_at,
        updated_at=record.updated_at,
        expires_at=record.expires_at,
        metadata=record.metadata,
    )
