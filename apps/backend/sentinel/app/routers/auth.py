from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import is_desktop_app, settings
from app.dependencies import get_manager_db
from app.middleware.auth import (
    Identity,
    ACCESS_TOKEN_COOKIE_NAME,
    REFRESH_TOKEN_COOKIE_NAME,
    TokenPayload,
    create_access_token,
    create_refresh_token,
    decode_and_validate_token,
    require_auth,
    revoke_token,
)
from app.middleware.audit import log_manager_audit
from app.schemas.auth import (
    AuthStatusResponse,
    BootstrapAuthRequest,
    AuthMeResponse,
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    TokenPairResponse,
)
from app.services.auth_service import (
    auth_is_configured,
    authenticate_user,
    bootstrap_auth_settings,
    change_user_password,
)

router = APIRouter()


def _set_auth_cookies(response: Response, *, access_token: str, refresh_token: str) -> None:
    secure = settings.auth_cookie_secure
    samesite = settings.auth_cookie_samesite
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=settings.access_token_ttl_seconds,
        path="/",
    )
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=settings.refresh_token_ttl_seconds,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_TOKEN_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_TOKEN_COOKIE_NAME, path="/")


@router.post("/login", response_model=TokenPairResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_manager_db),
) -> TokenPairResponse:
    auth = await authenticate_user(
        db,
        username=payload.username,
        password=payload.password,
    )
    if auth is None:
        await log_manager_audit(
            db,
            user_id=payload.username,
            action="auth.login.failed",
            status_code=401,
            ip_address=request.client.host if request.client else None,
            request_id=getattr(request.state, "request_id", None),
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    identity = Identity(user_id=auth[0], role=auth[1], agent_id=None)
    access_token = create_access_token(identity)
    refresh_token = create_refresh_token(identity)
    _set_auth_cookies(response, access_token=access_token, refresh_token=refresh_token)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    await log_manager_audit(
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


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(db: AsyncSession = Depends(get_manager_db)) -> AuthStatusResponse:
    configured = await auth_is_configured(db)
    return AuthStatusResponse(
        configured=configured,
        bootstrap_available=is_desktop_app() and not configured,
    )


@router.post("/bootstrap", response_model=TokenPairResponse)
async def bootstrap_auth(
    payload: BootstrapAuthRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_manager_db),
) -> TokenPairResponse:
    if not is_desktop_app():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Auth bootstrap is only available in desktop mode")
    created = await bootstrap_auth_settings(
        db,
        username=payload.username,
        password=payload.password,
    )
    if not created:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Auth is already configured")

    identity = Identity(user_id=payload.username.strip().lower(), role="admin", agent_id=None)
    access_token = create_access_token(identity)
    refresh_token = create_refresh_token(identity)
    _set_auth_cookies(response, access_token=access_token, refresh_token=refresh_token)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    await log_manager_audit(
        db,
        user_id=identity.user_id,
        action="auth.bootstrap",
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
    request: Request,
    response: Response,
    payload: RefreshRequest | None = None,
    db: AsyncSession = Depends(get_manager_db),
) -> TokenPairResponse:
    token = ""
    if payload is not None:
        token = payload.refresh_token.strip()
    if not token:
        token = request.cookies.get(REFRESH_TOKEN_COOKIE_NAME, "").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")

    token_payload = await decode_and_validate_token(token, db, expected_type="refresh")
    identity = Identity(user_id=token_payload.sub, role=token_payload.role, agent_id=token_payload.agent_id)
    access_token = create_access_token(identity)
    refresh_token = create_refresh_token(identity)
    _set_auth_cookies(response, access_token=access_token, refresh_token=refresh_token)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenPairResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.access_token_ttl_seconds,
    )


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_manager_db),
) -> dict[str, bool]:
    # Server/compose mode: env vars are the source of truth and force-sync on
    # every restart would revert any UI change, so the endpoint is hidden.
    # Use the env-rotation flow (edit .env + restart) instead.
    if not is_desktop_app():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Password change via API is only available in desktop mode. "
                "Rotate SENTINEL_AUTH_PASSWORD in .env and restart the backend."
            ),
        )
    changed = await change_user_password(
        db,
        username=user.sub,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    if not changed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is invalid",
        )
    return {"success": True}


@router.get("/me", response_model=AuthMeResponse)
async def me(user: TokenPayload = Depends(require_auth)) -> AuthMeResponse:
    return AuthMeResponse(sub=user.sub, role=user.role, agent_id=user.agent_id)


@router.delete("/session")
async def delete_session(
    request: Request,
    response: Response,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_manager_db),
) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    refresh_cookie = request.cookies.get(REFRESH_TOKEN_COOKIE_NAME, "").strip()
    await revoke_token(db, user)
    if refresh_cookie:
        try:
            refresh_payload = await decode_and_validate_token(
                refresh_cookie,
                db,
                expected_type="refresh",
            )
            await revoke_token(db, refresh_payload)
        except Exception:
            pass
    _clear_auth_cookies(response)
    await log_manager_audit(
        db,
        user_id=user.sub,
        action="auth.logout",
        status_code=200,
        ip_address=request.client.host if request.client else None,
        request_id=getattr(request.state, "request_id", None),
    )
    return {"status": "revoked"}
