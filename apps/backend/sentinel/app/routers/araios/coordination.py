"""AraiOS Coordination router — async SQLAlchemy."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, Query

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.models.araios import AraiosCoordinationMessage, AraiosPermission, AraiosApproval, araios_gen_id
from app.schemas.araios import CoordinationSend, CoordinationMessageOut, CoordinationListResponse


router = APIRouter(tags=["araios-coordination"])


# ── Helpers ──


def _require_araios_permission(action: str):
    async def _check(
        user: TokenPayload = Depends(require_auth),
        db: AsyncSession = Depends(get_db),
    ):
        from fastapi import HTTPException

        if user.role == "admin":
            return
        result = await db.execute(select(AraiosPermission).where(AraiosPermission.action == action))
        perm = result.scalars().first()
        level = perm.level if perm else "deny"
        if level == "allow":
            return
        if level == "deny":
            raise HTTPException(status_code=403, detail=f"Action '{action}' is not allowed for agent role")
        if level == "approval":
            approval = AraiosApproval(
                id=araios_gen_id(),
                status="pending",
                action=action,
                description=f"Agent requested: {action}",
            )
            db.add(approval)
            await db.commit()
            await db.refresh(approval)
            raise HTTPException(
                status_code=202,
                detail={
                    "message": "Action requires approval",
                    "approval": {"id": approval.id, "status": approval.status, "action": approval.action},
                },
            )

    return _check


def _get_agent_id(user: TokenPayload = Depends(require_auth)) -> str:
    return user.agent_id or user.sub


# ── Routes ──


@router.post("", response_model=CoordinationMessageOut, status_code=201)
async def send_message(
    body: CoordinationSend,
    agent_id: str = Depends(_get_agent_id),
    _perm: None = Depends(_require_araios_permission("coordination.send")),
    db: AsyncSession = Depends(get_db),
):
    msg = AraiosCoordinationMessage(
        id=araios_gen_id(),
        agent=agent_id,
        message=body.message,
        context=body.context,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return CoordinationMessageOut(
        id=msg.id,
        agent=msg.agent,
        message=msg.message,
        context=msg.context,
        createdAt=msg.created_at.isoformat() if msg.created_at else None,
    )


@router.get("", response_model=CoordinationListResponse)
async def list_messages(
    limit: int = Query(50, ge=1, le=500),
    _user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AraiosCoordinationMessage)
        .order_by(AraiosCoordinationMessage.seq.asc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return CoordinationListResponse(
        messages=[
            CoordinationMessageOut(
                id=r.id,
                agent=r.agent,
                message=r.message,
                context=r.context,
                createdAt=r.created_at.isoformat() if r.created_at else None,
            )
            for r in rows
        ]
    )
