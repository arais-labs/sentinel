from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Approval, Permission, RevokedToken, gen_id
from app.dependencies import get_db
from app.platform_auth import decode_token

ACCESS_TOKEN_COOKIE_NAME = "araios_access_token"
REFRESH_TOKEN_COOKIE_NAME = "araios_refresh_token"


class TokenPayload(BaseModel):
    sub: str
    role: str
    agent_id: str | None = None
    label: str | None = None
    exp: int
    iat: int
    jti: str
    token_type: str


def _token_from_request(request: Request, *, token_type: str) -> str:
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not token:
        cookie_key = ACCESS_TOKEN_COOKIE_NAME if token_type == "access" else REFRESH_TOKEN_COOKIE_NAME
        token = request.cookies.get(cookie_key, "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    return token


def _is_revoked(db: Session, jti: str) -> bool:
    row = db.execute(select(RevokedToken).where(RevokedToken.jti == jti)).scalar_one_or_none()
    return row is not None


def decode_and_validate_token(
    token: str, db: Session, *, expected_type: str | None = None
) -> TokenPayload:
    payload = decode_token(token, expected_type=expected_type)
    parsed = TokenPayload.model_validate(payload)
    if _is_revoked(db, parsed.jti):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return parsed


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> TokenPayload:
    token = _token_from_request(request, token_type="access")
    return decode_and_validate_token(token, db, expected_type="access")


def get_role(user: TokenPayload = Depends(get_current_user)) -> str:
    return user.role


def get_agent_id(user: TokenPayload = Depends(get_current_user)) -> str:
    return user.agent_id or user.sub


def get_subject(user: TokenPayload = Depends(get_current_user)) -> str:
    return user.sub


def revoke_token(db: Session, payload: TokenPayload) -> None:
    expires_at = datetime.fromtimestamp(payload.exp, tz=UTC)
    if _is_revoked(db, payload.jti):
        return
    db.add(RevokedToken(jti=payload.jti, expires_at=expires_at))
    db.commit()


def require_permission(action: str):
    """Return a FastAPI dependency that checks permission for the given action.

    - Admin: always allowed
    - Agent + allow: allowed
    - Agent + approval: creates an approval record and returns 202
    - Agent + deny: returns 403
    """

    async def _check(
        request: Request,
        role: str = Depends(get_role),
        db: Session = Depends(get_db),
    ):
        if role == "admin":
            return

        row = db.query(Permission).filter(Permission.action == action).first()
        perm = row.level if row else "deny"

        if perm == "allow":
            return

        if perm == "deny":
            raise HTTPException(status_code=403, detail=f"Action '{action}' is not allowed for agent role")

        if perm == "approval":
            body = None
            if request.method in ("POST", "PATCH", "PUT"):
                try:
                    body = await request.json()
                except Exception:
                    body = None

            resource_id = (
                request.path_params.get("id")
                or request.path_params.get("name")
                or request.path_params.get("slug")
                or request.path_params.get(
                    next((k for k in request.path_params if k.endswith("_id")), ""), None
                )
            )

            resource = action.rsplit(".", 1)[0] if "." in action else action

            approval = Approval(
                id=gen_id(),
                status="pending",
                action=action,
                resource=resource,
                resource_id=resource_id,
                description=f"Agent requested: {action}" + (f" on {resource_id}" if resource_id else ""),
                payload=body,
            )
            db.add(approval)
            db.commit()
            db.refresh(approval)

            raise HTTPException(
                status_code=status.HTTP_202_ACCEPTED,
                detail={
                    "message": "Action requires approval",
                    "approval": {
                        "id": approval.id,
                        "status": approval.status,
                        "action": approval.action,
                        "description": approval.description,
                    },
                },
            )

    return _check
