from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models import RevokedToken

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


class TokenPayload(BaseModel):
    sub: str
    role: str
    agent_id: str | None = None
    exp: int
    iat: int
    jti: str
    token_type: str


class Identity(BaseModel):
    user_id: str
    role: str
    agent_id: str | None = None


def _encode_token(*, identity: Identity, token_type: str, ttl_seconds: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": identity.user_id,
        "role": identity.role,
        "agent_id": identity.agent_id,
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "iat": int(now.timestamp()),
        "jti": str(uuid.uuid4()),
        "token_type": token_type,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(identity: Identity) -> str:
    return _encode_token(identity=identity, token_type="access", ttl_seconds=settings.access_token_ttl_seconds)


def create_refresh_token(identity: Identity) -> str:
    return _encode_token(identity=identity, token_type="refresh", ttl_seconds=settings.refresh_token_ttl_seconds)


async def _is_revoked(db: AsyncSession, jti: str) -> bool:
    try:
        token_id = uuid.UUID(jti)
    except ValueError:
        return True

    result = await db.execute(select(RevokedToken).where(RevokedToken.jti == token_id))
    return result.scalar_one_or_none() is not None


async def decode_and_validate_token(
    token: str, db: AsyncSession, *, expected_type: str | None = None
) -> TokenPayload:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        parsed = TokenPayload.model_validate(payload)
    except (jwt.PyJWTError, ValueError):
        raise unauthorized

    if expected_type and parsed.token_type != expected_type:
        raise unauthorized

    if await _is_revoked(db, parsed.jti):
        raise unauthorized

    return parsed


async def get_current_user(
    token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)
) -> TokenPayload:
    return await decode_and_validate_token(token, db, expected_type="access")


async def require_auth(user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
    return user


async def require_admin(user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


async def revoke_token(db: AsyncSession, payload: TokenPayload) -> None:
    expires_at = datetime.fromtimestamp(payload.exp, tz=UTC)
    record = RevokedToken(jti=uuid.UUID(payload.jti), expires_at=expires_at)
    db.add(record)
    await db.commit()


async def resolve_identity_from_araios(araios_token: str) -> Identity:
    if not settings.araios_url:
        if araios_token != settings.dev_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid dev token")
        return Identity(user_id=settings.dev_user_id, role="admin", agent_id=settings.dev_agent_id)

    verify_url = f"{settings.araios_url.rstrip('/')}/api/verify"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            verify_url,
            headers={"Authorization": f"Bearer {araios_token}"},
        )

    if response.status_code != status.HTTP_200_OK:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid araiOS token")

    data = response.json()
    user_id = data.get("user_id") or data.get("sub")
    role = data.get("role", "agent")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid araiOS verify payload")
    return Identity(user_id=user_id, role=role, agent_id=data.get("agent_id"))

