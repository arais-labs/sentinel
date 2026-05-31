"""Shared helpers for persisted system settings rows."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system import SystemSetting


async def get_system_setting(db: AsyncSession, *, key: str) -> str | None:
    """Fetch one system setting value by key."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalars().first()
    return setting.value if setting is not None else None


async def upsert_system_setting(db: AsyncSession, *, key: str, value: str) -> None:
    """Insert or update one system setting value in the given DB session."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalars().first()
    if setting is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        setting.value = value
    await db.commit()


async def delete_system_setting(db: AsyncSession, *, key: str) -> None:
    """Delete one system setting row when it exists in the given DB session."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalars().first()
    if setting is not None:
        await db.delete(setting)
        await db.commit()
