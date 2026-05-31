from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Session as SessionModel
from app.services.sessions import session_bindings
from app.services.settings.system_settings import delete_system_setting, upsert_system_setting

logger = logging.getLogger(__name__)


def mask_telegram_token(value: str | None) -> str | None:
    """Return a display-safe token preview for UI/status payloads."""
    if not value:
        return None
    if len(value) <= 8:
        return "****"
    return value[:4] + "..." + value[-4:]


async def _upsert_setting(
    session_factory: async_sessionmaker[AsyncSession], key: str, value: str
) -> None:
    """Insert or update a single system setting key/value."""
    async with session_factory() as db:
        await upsert_system_setting(db, key=key, value=value)


async def _delete_setting(session_factory: async_sessionmaker[AsyncSession], key: str) -> None:
    """Delete a system setting key when present."""
    async with session_factory() as db:
        await delete_system_setting(db, key=key)


async def persist_telegram_settings(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    bot_token: str,
    owner_user_id: str,
    owner_chat_id: str | None = None,
    owner_telegram_user_id: str | None = None,
) -> None:
    """Persist integration settings to DB-backed system settings keys."""
    await _upsert_setting(session_factory, "telegram_bot_token", bot_token)
    await _upsert_setting(session_factory, "telegram_owner_user_id", owner_user_id)
    if owner_chat_id:
        await _upsert_setting(session_factory, "telegram_owner_chat_id", owner_chat_id)
    else:
        await _delete_setting(session_factory, "telegram_owner_chat_id")
    if owner_telegram_user_id:
        await _upsert_setting(
            session_factory, "telegram_owner_telegram_user_id", owner_telegram_user_id
        )
    else:
        await _delete_setting(session_factory, "telegram_owner_telegram_user_id")


async def resolve_latest_active_root_session_id_for_user(
    session_factory: async_sessionmaker[AsyncSession], user_id: str
) -> str | None:
    """Return newest active root session id for a user, if any."""
    try:
        async with session_factory() as db:
            session_id = await session_bindings.resolve_main_session_id(db, user_id=user_id)
            return str(session_id) if session_id is not None else None
    except Exception:
        return None


async def resolve_owner_user_id_from_session(
    session_factory: async_sessionmaker[AsyncSession], session_id: str | None
) -> str | None:
    """Resolve owner user_id from an explicit Sentinel session id."""
    if not session_id:
        return None
    try:
        parsed = UUID(session_id)
    except (ValueError, TypeError):
        return None

    async with session_factory() as db:
        result = await db.execute(select(SessionModel).where(SessionModel.id == parsed))
        session = result.scalars().first()
        if session is None:
            return None
        return session.user_id


__all__ = [
    "mask_telegram_token",
    "persist_telegram_settings",
    "resolve_latest_active_root_session_id_for_user",
    "resolve_owner_user_id_from_session",
]
