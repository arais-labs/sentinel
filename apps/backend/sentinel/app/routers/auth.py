from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.middleware.audit import log_audit
from app.middleware.auth import (
    Identity,
    TokenPayload,
    create_access_token,
    create_refresh_token,
    decode_and_validate_token,
    require_auth,
    resolve_identity_from_araios,
    revoke_token,
)
from app.schemas.auth import RefreshRequest, TokenExchangeRequest, TokenPairResponse

router = APIRouter()


@router.post("/token", response_model=TokenPairResponse)
async def create_session_token(
    payload: TokenExchangeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenPairResponse:
    identity = await resolve_identity_from_araios(payload.araios_token)
    access_token = create_access_token(identity)
    refresh_token = create_refresh_token(identity)
    await log_audit(
        db,
        user_id=identity.user_id,
        action="auth.login",
        status_code=200,
        ip_address=request.client.host if request.client else None,
        request_id=getattr(request.state, "request_id", None),
    )
    return TokenPairResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.access_token_ttl_seconds,
    )


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh_session_token(
    payload: RefreshRequest, db: AsyncSession = Depends(get_db)
) -> TokenPairResponse:
    token_payload = await decode_and_validate_token(payload.refresh_token, db, expected_type="refresh")
    identity = Identity(user_id=token_payload.sub, role=token_payload.role, agent_id=token_payload.agent_id)
    access_token = create_access_token(identity)
    refresh_token = create_refresh_token(identity)
    return TokenPairResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.access_token_ttl_seconds,
    )


@router.delete("/session")
async def delete_session(
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    await revoke_token(db, user)
    await log_audit(
        db,
        user_id=user.sub,
        action="auth.logout",
        status_code=200,
        ip_address=request.client.host if request.client else None,
        request_id=getattr(request.state, "request_id", None),
    )
    return {"status": "revoked"}
