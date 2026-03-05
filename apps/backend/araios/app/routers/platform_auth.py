from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.database.models import PlatformApiKey, SystemSetting
from app.dependencies import get_db
from app.middleware.auth import (
    ACCESS_TOKEN_COOKIE_NAME,
    REFRESH_TOKEN_COOKIE_NAME,
    TokenPayload,
    decode_and_validate_token,
    get_current_user,
    revoke_token,
)
from app.platform_auth import (
    PlatformIdentity,
    create_access_token,
    create_refresh_token,
    hash_api_key,
    verify_api_key,
)
from app.services.auth_settings import authenticate_user, change_user_password
from config import (
    ACCESS_TOKEN_TTL_SECONDS,
    AUTH_COOKIE_SAMESITE,
    AUTH_COOKIE_SECURE,
    REFRESH_TOKEN_TTL_SECONDS,
)

router = APIRouter()


def _set_auth_cookies(response: Response, *, access_token: str, refresh_token: str) -> None:
    secure = AUTH_COOKIE_SECURE
    samesite = AUTH_COOKIE_SAMESITE
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=ACCESS_TOKEN_TTL_SECONDS,
        path="/",
    )
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=REFRESH_TOKEN_TTL_SECONDS,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_TOKEN_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_TOKEN_COOKIE_NAME, path="/")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)

    @field_validator("username", "password")
    @classmethod
    def _normalize_required(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Fields must not be empty")
        return trimmed


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


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)

    @field_validator("current_password", "new_password")
    @classmethod
    def _normalize_password_fields(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("password fields must not be empty")
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


class UpdateAgentKeyRequest(BaseModel):
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


class AppLinksResponse(BaseModel):
    sentinel_frontend_url: str | None = None
    araios_frontend_url: str | None = None


def _identity_from_key(record: PlatformApiKey) -> PlatformIdentity:
    return PlatformIdentity(
        sub=record.subject,
        role=record.role,
        agent_id=record.agent_id,
        label=record.label,
    )


def _new_agent_api_key() -> str:
    return f"sk-arais-agent-{secrets.token_urlsafe(32)}"


def _to_agent_summary(record: PlatformApiKey) -> AgentKeySummary:
    return AgentKeySummary(
        id=record.id,
        label=record.label,
        subject=record.subject,
        agent_id=record.agent_id,
        is_active=record.is_active,
        created_at=record.created_at.isoformat() if record.created_at else None,
    )


def _active_agent_keys(db: Session) -> list[PlatformApiKey]:
    return (
        db.query(PlatformApiKey)
        .filter(
            PlatformApiKey.role == "agent",
            PlatformApiKey.is_active.is_(True),
        )
        .all()
    )


def _get_agent_key_or_404(db: Session, agent_key_id: str) -> PlatformApiKey:
    record = (
        db.query(PlatformApiKey)
        .filter(
            PlatformApiKey.id == agent_key_id,
            PlatformApiKey.role == "agent",
        )
        .first()
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent key not found")
    return record


def _require_admin(user: TokenPayload) -> None:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")


def _token_response_for_identity(identity: PlatformIdentity) -> TokenPairResponse:
    return TokenPairResponse(
        access_token=create_access_token(identity),
        refresh_token=create_refresh_token(identity),
        token_type="bearer",
        expires_in=ACCESS_TOKEN_TTL_SECONDS,
    )


def _get_system_setting_value(db: Session, key: str) -> str | None:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row is None:
        return None
    value = row.value.strip()
    return value or None


@router.post("/login", response_model=TokenPairResponse)
async def login(
    body: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenPairResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"

    auth = authenticate_user(db, username=body.username, password=body.password)
    if auth is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    tokens = _token_response_for_identity(
        PlatformIdentity(sub=auth[0], role=auth[1], agent_id="admin", label="Primary Admin")
    )
    _set_auth_cookies(response, access_token=tokens.access_token, refresh_token=tokens.refresh_token)
    return tokens


@router.post("/token", response_model=TokenPairResponse)
async def issue_tokens(
    body: TokenRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenPairResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"

    keys = _active_agent_keys(db)
    identity: PlatformIdentity | None = None
    for record in keys:
        if verify_api_key(body.api_key, record.key_hash):
            identity = _identity_from_key(record)
            break

    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    tokens = _token_response_for_identity(identity)
    _set_auth_cookies(response, access_token=tokens.access_token, refresh_token=tokens.refresh_token)
    return tokens


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh_tokens(
    request: Request,
    response: Response,
    body: RefreshRequest | None = None,
    db: Session = Depends(get_db),
) -> TokenPairResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    token = ""
    if body is not None:
        token = body.refresh_token.strip()
    if not token:
        token = request.cookies.get(REFRESH_TOKEN_COOKIE_NAME, "").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")

    payload = decode_and_validate_token(token, db, expected_type="refresh")
    tokens = _token_response_for_identity(
        PlatformIdentity(
            sub=payload.sub,
            role=payload.role,
            agent_id=payload.agent_id,
            label=payload.label,
        )
    )
    _set_auth_cookies(response, access_token=tokens.access_token, refresh_token=tokens.refresh_token)
    return tokens


@router.get("/me", response_model=MeResponse)
async def me(user: TokenPayload = Depends(get_current_user)) -> MeResponse:
    return MeResponse(sub=user.sub, role=user.role, agent_id=user.agent_id, label=user.label)


@router.get("/app-links", response_model=AppLinksResponse)
async def app_links(
    _: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AppLinksResponse:
    return AppLinksResponse(
        sentinel_frontend_url=_get_system_setting_value(db, "sentinel_frontend_url"),
        araios_frontend_url=_get_system_setting_value(db, "araios_frontend_url"),
    )


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    _require_admin(user)
    changed = change_user_password(
        db,
        username=user.sub,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    if not changed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is invalid")
    return {"success": True}


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
            PlatformApiKey.is_active.is_(True),
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
            PlatformApiKey.is_active.is_(True),
            PlatformApiKey.agent_id == agent_id,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="agent_id already exists")

    api_key = _new_agent_api_key()
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


@router.patch("/agents/{agent_key_id}", response_model=AgentKeySummary)
async def update_agent_key(
    agent_key_id: str,
    body: UpdateAgentKeyRequest,
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AgentKeySummary:
    _require_admin(user)
    record = _get_agent_key_or_404(db, agent_key_id)

    fields = body.model_fields_set
    if "agent_id" in fields:
        if body.agent_id is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="agent_id cannot be empty")
        existing = (
            db.query(PlatformApiKey)
            .filter(
                PlatformApiKey.role == "agent",
                PlatformApiKey.is_active.is_(True),
                PlatformApiKey.agent_id == body.agent_id,
                PlatformApiKey.id != record.id,
            )
            .first()
        )
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="agent_id already exists")
        record.agent_id = body.agent_id

    if "label" in fields:
        if body.label is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="label cannot be empty")
        record.label = body.label
    if "subject" in fields:
        if body.subject is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="subject cannot be empty")
        record.subject = body.subject

    db.commit()
    db.refresh(record)
    return _to_agent_summary(record)


@router.post("/agents/{agent_key_id}/rotate", response_model=CreateAgentKeyResponse)
async def rotate_agent_key(
    agent_key_id: str,
    response: Response,
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateAgentKeyResponse:
    _require_admin(user)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    record = _get_agent_key_or_404(db, agent_key_id)

    api_key = _new_agent_api_key()
    record.key_hash = hash_api_key(api_key)
    record.is_active = True
    db.commit()
    db.refresh(record)
    return CreateAgentKeyResponse(agent=_to_agent_summary(record), api_key=api_key)


@router.delete("/agents/{agent_key_id}")
async def deactivate_agent_key(
    agent_key_id: str,
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    _require_admin(user)
    record = _get_agent_key_or_404(db, agent_key_id)

    if record.is_active:
        record.is_active = False
        db.commit()

    return {"success": True}


@router.delete("/session")
async def logout(
    request: Request,
    response: Response,
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    revoke_token(db, user)
    refresh_cookie = request.cookies.get(REFRESH_TOKEN_COOKIE_NAME, "").strip()
    if refresh_cookie:
        try:
            refresh_payload = decode_and_validate_token(refresh_cookie, db, expected_type="refresh")
            revoke_token(db, refresh_payload)
        except Exception:
            pass
    _clear_auth_cookies(response)
    return {"status": "ok"}
