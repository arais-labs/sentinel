from __future__ import annotations

import hashlib
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.settings.manager_settings import get_manager_setting, upsert_manager_setting

_USERNAME_KEY = "sentinel.auth.username"
_PASSWORD_HASH_KEY = "sentinel.auth.password_hash"
_PASSWORD_HASH_ROUNDS = 240_000


def _normalize_username(value: str) -> str:
    return value.strip().lower()


def _hash_password(password: str, *, salt: str | None = None) -> str:
    salt_value = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_value.encode("utf-8"),
        _PASSWORD_HASH_ROUNDS,
    )
    return f"{salt_value}${digest.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, _ = stored_hash.split("$", 1)
    except ValueError:
        return False
    candidate = _hash_password(password, salt=salt)
    return secrets.compare_digest(candidate, stored_hash)


def _is_desktop_mode() -> bool:
    return (settings.app_env or "").strip().lower() == "desktop"


async def ensure_default_auth_settings(db: AsyncSession) -> None:
    username = await get_manager_setting(db, key=_USERNAME_KEY)
    password_hash = await get_manager_setting(db, key=_PASSWORD_HASH_KEY)
    seed_username = _normalize_username(settings.sentinel_auth_username)
    seed_password = settings.sentinel_auth_password.strip()

    if _is_desktop_mode():
        # Desktop: DB wins; env only seeds an empty DB on first run.
        if username and password_hash:
            return
        if not seed_username or not seed_password:
            return
        await upsert_manager_setting(db, key=_USERNAME_KEY, value=seed_username)
        await upsert_manager_setting(
            db, key=_PASSWORD_HASH_KEY, value=_hash_password(seed_password)
        )
        return

    if not seed_username or not seed_password:
        raise RuntimeError(
            "SENTINEL_AUTH_USERNAME and SENTINEL_AUTH_PASSWORD must be set "
            "(non-empty) outside desktop mode."
        )
    if not username or _normalize_username(username) != seed_username:
        await upsert_manager_setting(db, key=_USERNAME_KEY, value=seed_username)
    if not password_hash or not _verify_password(seed_password, password_hash):
        await upsert_manager_setting(
            db, key=_PASSWORD_HASH_KEY, value=_hash_password(seed_password)
        )


async def auth_is_configured(db: AsyncSession) -> bool:
    username = await get_manager_setting(db, key=_USERNAME_KEY)
    password_hash = await get_manager_setting(db, key=_PASSWORD_HASH_KEY)
    return bool(username and password_hash)


async def bootstrap_auth_settings(
    db: AsyncSession,
    *,
    username: str,
    password: str,
) -> bool:
    if not _is_desktop_mode():
        return False
    if await auth_is_configured(db):
        return False
    normalized_username = _normalize_username(username)
    normalized_password = password.strip()
    if not normalized_username or not normalized_password:
        return False
    await upsert_manager_setting(db, key=_USERNAME_KEY, value=normalized_username)
    await upsert_manager_setting(
        db, key=_PASSWORD_HASH_KEY, value=_hash_password(normalized_password)
    )
    return True


async def change_user_password(
    db: AsyncSession,
    *,
    username: str,
    current_password: str,
    new_password: str,
) -> bool:
    # Desktop-only: in server mode the next startup's force-sync reverts this.
    stored_username = await get_manager_setting(db, key=_USERNAME_KEY)
    stored_hash = await get_manager_setting(db, key=_PASSWORD_HASH_KEY)
    if not stored_username or not stored_hash:
        return False
    if _normalize_username(username) != _normalize_username(stored_username):
        return False
    if not _verify_password(current_password, stored_hash):
        return False
    await upsert_manager_setting(
        db, key=_PASSWORD_HASH_KEY, value=_hash_password(new_password)
    )
    return True


async def authenticate_user(
    db: AsyncSession,
    *,
    username: str,
    password: str,
) -> tuple[str, str] | None:
    stored_username = await get_manager_setting(db, key=_USERNAME_KEY)
    stored_hash = await get_manager_setting(db, key=_PASSWORD_HASH_KEY)
    if not stored_username or not stored_hash:
        return None

    normalized = _normalize_username(username)
    if normalized != _normalize_username(stored_username):
        return None

    if not _verify_password(password, stored_hash):
        return None

    return (stored_username, "admin")
