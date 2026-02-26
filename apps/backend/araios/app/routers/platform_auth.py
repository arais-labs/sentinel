from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.database.models import PlatformApiKey
from app.dependencies import get_db
from app.middleware.auth import TokenPayload, get_current_user
from app.platform_auth import (
    PlatformIdentity,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_api_key,
    verify_api_key,
)
from config import (
    ACCESS_TOKEN_TTL_SECONDS,
    PLATFORM_BOOTSTRAP_AGENT_ID,
    PLATFORM_BOOTSTRAP_LABEL,
    PLATFORM_BOOTSTRAP_ROLE,
    PLATFORM_BOOTSTRAP_SUB,
)

router = APIRouter()


class TokenRequest(BaseModel):
    api_key: str = Field(min_length=1)

    @field_validator("api_key")
    @classmethod
    def _normalize_api_key(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("api_key must not be empty")
        return trimmed


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)

    @field_validator("refresh_token")
    @classmethod
    def _normalize_refresh_token(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("refresh_token must not be empty")
        return trimmed


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    sub: str
    role: str
    agent_id: str | None = None
    label: str | None = None


class BootstrapFinalizeResponse(BaseModel):
    rotated: bool
    admin_api_key: str
    agent_api_key: str


class AgentKeySummary(BaseModel):
    id: str
    label: str
    subject: str
    agent_id: str | None = None
    is_active: bool
    created_at: str | None = None


class AgentKeyListResponse(BaseModel):
    agents: list[AgentKeySummary]


class CreateAgentKeyRequest(BaseModel):
    label: str | None = None
    subject: str | None = None
    agent_id: str | None = None

    @field_validator("label", "subject", "agent_id")
    @classmethod
    def _normalize_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class CreateAgentKeyResponse(BaseModel):
    agent: AgentKeySummary
    api_key: str


def _identity_from_key(record: PlatformApiKey) -> PlatformIdentity:
    return PlatformIdentity(
        sub=record.subject,
        role=record.role,
        agent_id=record.agent_id,
        label=record.label,
    )


def _is_bootstrap_record(record: PlatformApiKey) -> bool:
    return (
        record.label == PLATFORM_BOOTSTRAP_LABEL
        and record.role == PLATFORM_BOOTSTRAP_ROLE
        and record.subject == PLATFORM_BOOTSTRAP_SUB
        and (record.agent_id or "") == (PLATFORM_BOOTSTRAP_AGENT_ID or "")
    )


def _is_bootstrap_identity(user: TokenPayload) -> bool:
    return (
        user.role == PLATFORM_BOOTSTRAP_ROLE
        and user.sub == PLATFORM_BOOTSTRAP_SUB
        and (user.agent_id or "") == (PLATFORM_BOOTSTRAP_AGENT_ID or "")
    )


def _new_platform_api_key(kind: str) -> str:
    return f"sk-arais-{kind}-{secrets.token_urlsafe(32)}"


def _to_agent_summary(record: PlatformApiKey) -> AgentKeySummary:
    return AgentKeySummary(
        id=record.id,
        label=record.label,
        subject=record.subject,
        agent_id=record.agent_id,
        is_active=record.is_active,
        created_at=record.created_at.isoformat() if record.created_at else None,
    )


def _require_admin(user: TokenPayload) -> None:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")


@router.post("/token", response_model=TokenPairResponse)
async def issue_tokens(
    body: TokenRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenPairResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"

    keys = db.query(PlatformApiKey).filter(PlatformApiKey.is_active == True).all()  # noqa: E712
    identity: PlatformIdentity | None = None
    for record in keys:
        if verify_api_key(body.api_key, record.key_hash):
            identity = _identity_from_key(record)
            break

    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    return TokenPairResponse(
        access_token=create_access_token(identity),
        refresh_token=create_refresh_token(identity),
        token_type="bearer",
        expires_in=ACCESS_TOKEN_TTL_SECONDS,
    )


@router.post("/bootstrap/finalize", response_model=BootstrapFinalizeResponse)
async def finalize_bootstrap(
    response: Response,
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BootstrapFinalizeResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"

    if not _is_bootstrap_identity(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bootstrap session required")

    query = db.query(PlatformApiKey).filter(PlatformApiKey.is_active == True)  # noqa: E712
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        query = query.with_for_update()
    keys = query.all()

    if len(keys) != 1 or not _is_bootstrap_record(keys[0]):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Bootstrap already finalized")

    bootstrap_record = keys[0]
    admin_api_key = _new_platform_api_key("admin")
    agent_api_key = _new_platform_api_key("agent")

    db.add(
        PlatformApiKey(
            label="Primary Admin",
            role="admin",
            subject="admin",
            agent_id="admin",
            key_hash=hash_api_key(admin_api_key),
            is_active=True,
        )
    )
    db.add(
        PlatformApiKey(
            label="Primary Agent",
            role="agent",
            subject="agent",
            agent_id="agent",
            key_hash=hash_api_key(agent_api_key),
            is_active=True,
        )
    )
    db.delete(bootstrap_record)
    db.commit()

    return BootstrapFinalizeResponse(
        rotated=True,
        admin_api_key=admin_api_key,
        agent_api_key=agent_api_key,
    )


@router.get("/agents", response_model=AgentKeyListResponse)
async def list_agent_keys(
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AgentKeyListResponse:
    _require_admin(user)
    rows = (
        db.query(PlatformApiKey)
        .filter(
            PlatformApiKey.role == "agent",
            PlatformApiKey.is_active == True,  # noqa: E712
        )
        .order_by(PlatformApiKey.created_at.desc())
        .all()
    )
    return AgentKeyListResponse(agents=[_to_agent_summary(row) for row in rows])


@router.post("/agents", response_model=CreateAgentKeyResponse, status_code=201)
async def create_agent_key(
    body: CreateAgentKeyRequest,
    response: Response,
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateAgentKeyResponse:
    _require_admin(user)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"

    agent_id = body.agent_id or f"agent-{secrets.token_hex(4)}"
    label = body.label or agent_id
    subject = body.subject or agent_id

    existing = (
        db.query(PlatformApiKey)
        .filter(
            PlatformApiKey.role == "agent",
            PlatformApiKey.is_active == True,  # noqa: E712
            PlatformApiKey.agent_id == agent_id,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="agent_id already exists")

    api_key = _new_platform_api_key("agent")
    record = PlatformApiKey(
        label=label,
        role="agent",
        subject=subject,
        agent_id=agent_id,
        key_hash=hash_api_key(api_key),
        is_active=True,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return CreateAgentKeyResponse(agent=_to_agent_summary(record), api_key=api_key)


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh_tokens(body: RefreshRequest) -> TokenPairResponse:
    payload = decode_token(body.refresh_token, expected_type="refresh")
    identity = PlatformIdentity(
        sub=payload["sub"],
        role=payload["role"],
        agent_id=payload.get("agent_id"),
        label=payload.get("label"),
    )
    return TokenPairResponse(
        access_token=create_access_token(identity),
        refresh_token=create_refresh_token(identity),
        token_type="bearer",
        expires_in=ACCESS_TOKEN_TTL_SECONDS,
    )


@router.get("/me", response_model=MeResponse)
async def me(user: TokenPayload = Depends(get_current_user)) -> MeResponse:
    return MeResponse(sub=user.sub, role=user.role, agent_id=user.agent_id, label=user.label)


@router.delete("/session")
async def logout(_request: Request) -> dict[str, str]:
    # Stateless JWT for now; client-side token deletion performs logout.
    return {"status": "ok"}
