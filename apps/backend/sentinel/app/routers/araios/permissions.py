"""AraiOS Permissions router — async SQLAlchemy."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth, require_admin
from app.models.araios import AraiosPermission
from app.schemas.araios import PermissionOut, PermissionUpdate, PermissionListResponse

router = APIRouter(tags=["araios-permissions"])


@router.get("", response_model=PermissionListResponse)
async def list_permissions(
    _user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AraiosPermission))
    rows = result.scalars().all()
    return PermissionListResponse(
        permissions=[PermissionOut(action=r.action, level=r.level) for r in rows]
    )


@router.patch("/{action:path}", response_model=PermissionOut)
async def update_permission(
    action: str,
    body: PermissionUpdate,
    _admin: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if body.level not in ("allow", "approval", "deny"):
        raise HTTPException(status_code=400, detail="level must be allow, approval, or deny")

    result = await db.execute(select(AraiosPermission).where(AraiosPermission.action == action))
    perm = result.scalars().first()

    if not perm:
        perm = AraiosPermission(action=action, level=body.level)
        db.add(perm)
    else:
        perm.level = body.level

    await db.commit()
    await db.refresh(perm)
    return PermissionOut(action=perm.action, level=perm.level)
