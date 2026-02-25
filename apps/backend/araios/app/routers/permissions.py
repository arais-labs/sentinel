from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.middleware.auth import get_role
from app.database.models import Permission
from app.schemas import PermissionOut, PermissionUpdate, PermissionListResponse

router = APIRouter()

VALID_LEVELS = {"allow", "approval", "deny"}


@router.get(
    "",
    response_model=PermissionListResponse,
    summary="List all permissions",
    description="Returns every action with its current permission level. Both admin and agent can read.",
)
async def list_permissions(
    db: Session = Depends(get_db),
    role: str = Depends(get_role),
):
    rows = db.query(Permission).order_by(Permission.action).all()
    return {"permissions": [{"action": r.action, "level": r.level} for r in rows]}


@router.patch(
    "/{action:path}",
    response_model=PermissionOut,
    summary="Update a permission level",
    description="Admin-only. Set a permission to allow, approval, or deny.",
)
async def update_permission(
    action: str,
    body: PermissionUpdate,
    db: Session = Depends(get_db),
    role: str = Depends(get_role),
):
    if role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can update permissions")

    if body.level not in VALID_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid level '{body.level}'. Must be one of: {', '.join(sorted(VALID_LEVELS))}",
        )

    perm = db.query(Permission).filter(Permission.action == action).first()
    if not perm:
        raise HTTPException(status_code=404, detail=f"Permission '{action}' not found")

    perm.level = body.level
    db.commit()
    return {"action": perm.action, "level": perm.level}
