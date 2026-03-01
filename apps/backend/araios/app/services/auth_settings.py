from __future__ import annotations

from sqlalchemy.orm import Session

from app.database.models import SystemSetting
from app.platform_auth import hash_secret, verify_secret
from config import ARAIOS_AUTH_PASSWORD, ARAIOS_AUTH_USERNAME

AUTH_USERNAME_KEY = "araios.auth.username"
AUTH_PASSWORD_HASH_KEY = "araios.auth.password_hash"


def _normalize_username(value: str) -> str:
    return value.strip().lower()


def _get_setting(db: Session, key: str) -> str | None:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return row.value if row is not None else None


def _upsert_setting(db: Session, key: str, value: str) -> None:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def ensure_default_auth_settings(db: Session) -> None:
    username = _get_setting(db, AUTH_USERNAME_KEY)
    password_hash = _get_setting(db, AUTH_PASSWORD_HASH_KEY)
    if username and password_hash:
        return

    seed_username = _normalize_username(ARAIOS_AUTH_USERNAME)
    seed_password = ARAIOS_AUTH_PASSWORD.strip()
    if not seed_username or not seed_password:
        raise RuntimeError("araiOS auth credentials are not configured")

    if not username:
        _upsert_setting(db, AUTH_USERNAME_KEY, seed_username)
    if not password_hash:
        _upsert_setting(db, AUTH_PASSWORD_HASH_KEY, hash_secret(seed_password))


def authenticate_user(db: Session, *, username: str, password: str) -> tuple[str, str] | None:
    stored_username = _get_setting(db, AUTH_USERNAME_KEY)
    stored_hash = _get_setting(db, AUTH_PASSWORD_HASH_KEY)
    if not stored_username or not stored_hash:
        return None

    if _normalize_username(username) != _normalize_username(stored_username):
        return None
    if not verify_secret(password, stored_hash):
        return None

    return (stored_username, "admin")


def change_user_password(
    db: Session,
    *,
    username: str,
    current_password: str,
    new_password: str,
) -> bool:
    stored_username = _get_setting(db, AUTH_USERNAME_KEY)
    stored_hash = _get_setting(db, AUTH_PASSWORD_HASH_KEY)
    if not stored_username or not stored_hash:
        return False

    if _normalize_username(username) != _normalize_username(stored_username):
        return False
    if not verify_secret(current_password, stored_hash):
        return False

    _upsert_setting(db, AUTH_PASSWORD_HASH_KEY, hash_secret(new_password))
    return True
