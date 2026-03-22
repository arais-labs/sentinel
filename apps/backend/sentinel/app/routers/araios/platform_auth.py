"""AraiOS platform auth — login/token/refresh/me/session endpoints.

Serves the /platform/auth/* routes that the AraiOS frontend expects.
Uses Sentinel's JWT infrastructure with AraiOS cookie names for backward compat.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models.araios import AraiOSPlatformApiKey, araios_gen_id
from app.models.system import SystemSetting
from app.models.tokens import RevokedToken

router = APIRouter()

# Use Sentinel's cookie names — one auth system, one set of cookies
ACCESS_TOKEN_COOKIE_NAME = "sentinel_access_token"
REFRESH_TOKEN_COOKIE_NAME = "sentinel_refresh_token"


# ── JWT helpers ──


def _create_token(*, sub: str, role: str, agent_id: str | None, label: str | None, token_type: str, ttl: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "role": role,
        "agent_id": agent_id,
        "label": label,
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "iat": int(now.timestamp()),
        "jti": secrets.token_hex(16),
        "token_type": token_type,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _decode_token(token: str, *, expected_type: str | None = None) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if expected_type and payload.get("token_type") != expected_type:
        raise HTTPException(status_code=401, detail="Invalid token type")
    return payload


def _hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _verify_api_key(key: str, key_hash: str) -> bool:
    return _hash_api_key(key) == key_hash


# ── Cookie helpers ──


def _set_auth_cookies(response: Response, *, access_token: str, refresh_token: str) -> None:
    for name, value, ttl in [
        (ACCESS_TOKEN_COOKIE_NAME, access_token, settings.access_token_ttl_seconds),
        (REFRESH_TOKEN_COOKIE_NAME, refresh_token, settings.refresh_token_ttl_seconds),
    ]:
        response.set_cookie(
            key=name, value=value, httponly=True,
            secure=settings.auth_cookie_secure,
            samesite=settings.auth_cookie_samesite,
            max_age=ttl, path="/",
        )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_TOKEN_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_TOKEN_COOKIE_NAME, path="/")


def _token_pair(*, sub: str, role: str, agent_id: str | None = None, label: str | None = None) -> dict:
    return {
        "access_token": _create_token(sub=sub, role=role, agent_id=agent_id, label=label, token_type="access", ttl=settings.access_token_ttl_seconds),
        "refresh_token": _create_token(sub=sub, role=role, agent_id=agent_id, label=label, token_type="refresh", ttl=settings.refresh_token_ttl_seconds),
        "token_type": "bearer",
        "expires_in": settings.access_token_ttl_seconds,
    }


def _token_from_request(request: Request, *, cookie_name: str) -> str:
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not token:
        token = request.cookies.get(cookie_name, "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    return token


async def _is_revoked(db: AsyncSession, jti: str) -> bool:
    result = await db.execute(select(RevokedToken).where(RevokedToken.jti == jti))
    return result.scalars().first() is not None


async def _revoke_token(db: AsyncSession, jti: str, expires_at: datetime) -> None:
    if await _is_revoked(db, jti):
        return
    db.add(RevokedToken(jti=jti, expires_at=expires_at))
    await db.commit()


async def _get_current_user(request: Request, db: AsyncSession) -> dict:
    token = _token_from_request(request, cookie_name=ACCESS_TOKEN_COOKIE_NAME)
    payload = _decode_token(token, expected_type="access")
    if await _is_revoked(db, payload["jti"]):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


# ── Request/Response models ──


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)

    @field_validator("username", "password")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class TokenRequest(BaseModel):
    api_key: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


# ── Routes ──


@router.post("/login")
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    if body.username != settings.araios_auth_username or body.password != settings.araios_auth_password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    tokens = _token_pair(sub=body.username, role="admin", agent_id="admin", label="Primary Admin")
    _set_auth_cookies(response, access_token=tokens["access_token"], refresh_token=tokens["refresh_token"])
    return tokens


@router.post("/token")
async def issue_tokens(body: TokenRequest, response: Response, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    result = await db.execute(
        select(AraiOSPlatformApiKey).where(
            AraiOSPlatformApiKey.role == "agent",
            AraiOSPlatformApiKey.is_active.is_(True),
        )
    )
    keys = result.scalars().all()
    for record in keys:
        if _verify_api_key(body.api_key, record.key_hash):
            tokens = _token_pair(sub=record.subject, role=record.role, agent_id=record.agent_id, label=record.label)
            _set_auth_cookies(response, access_token=tokens["access_token"], refresh_token=tokens["refresh_token"])
            return tokens
    raise HTTPException(status_code=401, detail="Invalid API key")


@router.post("/refresh")
async def refresh_tokens(request: Request, response: Response, body: RefreshRequest | None = None, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    token = ""
    if body is not None:
        token = body.refresh_token.strip()
    if not token:
        token = request.cookies.get(REFRESH_TOKEN_COOKIE_NAME, "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    payload = _decode_token(token, expected_type="refresh")
    if await _is_revoked(db, payload["jti"]):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    tokens = _token_pair(sub=payload["sub"], role=payload["role"], agent_id=payload.get("agent_id"), label=payload.get("label"))
    _set_auth_cookies(response, access_token=tokens["access_token"], refresh_token=tokens["refresh_token"])
    return tokens


@router.get("/me")
async def me(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await _get_current_user(request, db)
    return {"sub": payload["sub"], "role": payload["role"], "agent_id": payload.get("agent_id"), "label": payload.get("label")}


@router.get("/app-links")
async def app_links(request: Request, db: AsyncSession = Depends(get_db)):
    await _get_current_user(request, db)
    return {
        "sentinel_frontend_url": "/sentinel",
        "araios_frontend_url": "/araios",
    }


@router.delete("/session")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    payload = await _get_current_user(request, db)
    await _revoke_token(db, payload["jti"], datetime.fromtimestamp(payload["exp"], tz=UTC))
    refresh_cookie = request.cookies.get(REFRESH_TOKEN_COOKIE_NAME, "").strip()
    if refresh_cookie:
        try:
            rp = _decode_token(refresh_cookie, expected_type="refresh")
            await _revoke_token(db, rp["jti"], datetime.fromtimestamp(rp["exp"], tz=UTC))
        except Exception:
            pass
    _clear_auth_cookies(response)
    return {"status": "ok"}


# ── Agent key management ──


@router.get("/agents")
async def list_agent_keys(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _get_current_user(request, db)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    result = await db.execute(
        select(AraiOSPlatformApiKey).where(
            AraiOSPlatformApiKey.role == "agent",
            AraiOSPlatformApiKey.is_active.is_(True),
        ).order_by(AraiOSPlatformApiKey.created_at.desc())
    )
    rows = result.scalars().all()
    return {"agents": [
        {"id": r.id, "label": r.label, "subject": r.subject, "agent_id": r.agent_id, "is_active": r.is_active, "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]}


@router.post("/agents", status_code=201)
async def create_agent_key(body: dict, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    user = await _get_current_user(request, db)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    response.headers["Cache-Control"] = "no-store"
    agent_id = body.get("agent_id") or f"agent-{secrets.token_hex(4)}"
    label = body.get("label") or agent_id
    subject = body.get("subject") or agent_id
    result = await db.execute(
        select(AraiOSPlatformApiKey).where(
            AraiOSPlatformApiKey.role == "agent",
            AraiOSPlatformApiKey.is_active.is_(True),
            AraiOSPlatformApiKey.agent_id == agent_id,
        )
    )
    if result.scalars().first():
        raise HTTPException(status_code=409, detail="agent_id already exists")
    api_key = f"sk-arais-agent-{secrets.token_urlsafe(32)}"
    record = AraiOSPlatformApiKey(label=label, role="agent", subject=subject, agent_id=agent_id, key_hash=_hash_api_key(api_key), is_active=True)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return {
        "agent": {"id": record.id, "label": record.label, "subject": record.subject, "agent_id": record.agent_id, "is_active": record.is_active, "created_at": record.created_at.isoformat() if record.created_at else None},
        "api_key": api_key,
    }


@router.delete("/agents/{agent_key_id}")
async def deactivate_agent_key(agent_key_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await _get_current_user(request, db)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    result = await db.execute(
        select(AraiOSPlatformApiKey).where(AraiOSPlatformApiKey.id == agent_key_id, AraiOSPlatformApiKey.role == "agent")
    )
    record = result.scalars().first()
    if not record:
        raise HTTPException(status_code=404, detail="Agent key not found")
    if record.is_active:
        record.is_active = False
        await db.commit()
    return {"success": True}
