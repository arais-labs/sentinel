from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.models import Approval, Permission, gen_id
from app.dependencies import get_db
from app.platform_auth import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/platform/auth/token")


class TokenPayload(BaseModel):
    sub: str
    role: str
    agent_id: str | None = None
    label: str | None = None
    exp: int
    iat: int
    jti: str
    token_type: str


def _bearer_token_from_request(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    return token


def _parse_access_token(token: str) -> TokenPayload:
    payload = decode_token(token, expected_type="access")
    return TokenPayload.model_validate(payload)


def get_role(request: Request) -> str:
    token = _bearer_token_from_request(request)
    payload = _parse_access_token(token)
    return payload.role


def get_agent_id(request: Request) -> str:
    token = _bearer_token_from_request(request)
    payload = _parse_access_token(token)
    return payload.agent_id or payload.sub


def get_subject(request: Request) -> str:
    token = _bearer_token_from_request(request)
    payload = _parse_access_token(token)
    return payload.sub


async def get_current_user(
    token: str = Depends(oauth2_scheme),
) -> TokenPayload:
    return _parse_access_token(token)


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
