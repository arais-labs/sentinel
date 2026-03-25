from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Session as SessionModel
from app.services.sessions import session_bindings
from app.services.settings.system_settings import delete_system_setting, upsert_system_setting

from .shared import TELEGRAM_OWNER_PAIRING_TTL_SECONDS

logger = logging.getLogger(__name__)


def mask_telegram_token(value: str | None) -> str | None:
    """Return a display-safe token preview for UI/status payloads."""
    if not value:
        return None
    if len(value) <= 8:
        return "****"
    return value[:4] + "..." + value[-4:]


async def _upsert_setting(key: str, value: str) -> None:
    """Insert or update a single system setting key/value."""
    async with AsyncSessionLocal() as db:
        await upsert_system_setting(db, key=key, value=value)


async def _delete_setting(key: str) -> None:
    """Delete a system setting key when present."""
    async with AsyncSessionLocal() as db:
        await delete_system_setting(db, key=key)


async def persist_telegram_settings(
    *,
    bot_token: str,
    owner_user_id: str,
    owner_chat_id: str | None = None,
    owner_telegram_user_id: str | None = None,
) -> None:
    """Persist integration settings to DB-backed system settings keys."""
    await _upsert_setting("telegram_bot_token", bot_token)
    await _upsert_setting("telegram_owner_user_id", owner_user_id)
    if owner_chat_id:
        await _upsert_setting("telegram_owner_chat_id", owner_chat_id)
    else:
        await _delete_setting("telegram_owner_chat_id")
    if owner_telegram_user_id:
        await _upsert_setting("telegram_owner_telegram_user_id", owner_telegram_user_id)
    else:
        await _delete_setting("telegram_owner_telegram_user_id")


async def clear_telegram_settings() -> None:
    """Remove all persisted Telegram integration settings and pairing state."""
    await _delete_setting("telegram_bot_token")
    await _delete_setting("telegram_owner_user_id")
    await _delete_setting("telegram_owner_chat_id")
    await _delete_setting("telegram_owner_telegram_user_id")
    await _delete_setting("telegram_pairing_code_hash")
    await _delete_setting("telegram_pairing_code_expires_at")


def _pairing_code_hash(raw_code: str) -> str:
    """Return SHA-256 digest for pairing code comparison."""
    return hashlib.sha256(raw_code.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    """UTC clock helper to keep datetime creation consistent."""
    return datetime.now(UTC)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse persisted ISO timestamp into timezone-aware UTC datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _pairing_not_expired(expires_at_iso: str | None) -> bool:
    """True when a pairing code expiry timestamp is still in the future."""
    expires_at = _parse_iso_datetime(expires_at_iso)
    return bool(expires_at and expires_at > _utcnow())


async def issue_owner_pairing_code() -> tuple[str, str]:
    """Create a short-lived owner pairing code for Telegram DM ownership linking."""
    code = secrets.token_hex(3).upper()
    expires_at = (_utcnow() + timedelta(seconds=TELEGRAM_OWNER_PAIRING_TTL_SECONDS)).isoformat()
    settings.telegram_pairing_code_hash = _pairing_code_hash(code)
    settings.telegram_pairing_code_expires_at = expires_at
    await _upsert_setting("telegram_pairing_code_hash", settings.telegram_pairing_code_hash)
    await _upsert_setting("telegram_pairing_code_expires_at", expires_at)
    return (code, expires_at)


async def clear_owner_pairing_code() -> None:
    """Clear in-memory and persisted owner pairing code state."""
    settings.telegram_pairing_code_hash = None
    settings.telegram_pairing_code_expires_at = None
    await _delete_setting("telegram_pairing_code_hash")
    await _delete_setting("telegram_pairing_code_expires_at")


async def stop_telegram_bridge(app_state: object) -> None:
    """Stop bridge worker/polling task and clear runtime app_state handles."""
    stop_event = getattr(app_state, "telegram_stop_event", None)
    bridge = getattr(app_state, "telegram_bridge", None)
    task = getattr(app_state, "telegram_task", None)

    if stop_event is not None:
        stop_event.set()

    if bridge is not None:
        await bridge.stop()

    if task is not None and not task.done():
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            pass

    app_state.telegram_bridge = None
    app_state.telegram_stop_event = None
    app_state.telegram_task = None


async def resolve_latest_active_root_session_id_for_user(user_id: str) -> str | None:
    """Return newest active root session id for a user, if any."""
    try:
        async with AsyncSessionLocal() as db:
            session_id = await session_bindings.resolve_main_session_id(db, user_id=user_id)
            return str(session_id) if session_id is not None else None
    except Exception:
        return None


async def start_telegram_bridge(app_state: object) -> bool:
    """Start bridge when token exists and sync owner/target route defaults first."""
    token = settings.telegram_bot_token
    if not token:
        return False

    await stop_telegram_bridge(app_state)

    from .bridge import TelegramBridge

    ws_manager = getattr(app_state, "ws_manager", None)
    run_registry = getattr(app_state, "agent_run_registry", None)
    agent_runtime_support = getattr(app_state, "agent_runtime_support", None)
    owner_user_id = settings.telegram_owner_user_id
    if not owner_user_id:
        owner_user_id = settings.dev_user_id

    bridge = TelegramBridge(
        bot_token=token,
        user_id=owner_user_id,
        agent_runtime_support=agent_runtime_support,
        run_registry=run_registry,
        ws_manager=ws_manager,
        db_factory=AsyncSessionLocal,
    )

    stop_event = asyncio.Event()
    task = asyncio.create_task(bridge.start(stop_event))
    app_state.telegram_bridge = bridge
    app_state.telegram_stop_event = stop_event
    app_state.telegram_task = task
    return True


async def resolve_owner_user_id_from_session(session_id: str | None) -> str | None:
    """Resolve owner user_id from an explicit Sentinel session id."""
    if not session_id:
        return None
    try:
        parsed = UUID(session_id)
    except (ValueError, TypeError):
        return None

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SessionModel).where(SessionModel.id == parsed))
        session = result.scalars().first()
        if session is None:
            return None
        return session.user_id


__all__ = [
    "clear_owner_pairing_code",
    "clear_telegram_settings",
    "issue_owner_pairing_code",
    "mask_telegram_token",
    "persist_telegram_settings",
    "resolve_latest_active_root_session_id_for_user",
    "resolve_owner_user_id_from_session",
    "start_telegram_bridge",
    "stop_telegram_bridge",
]
