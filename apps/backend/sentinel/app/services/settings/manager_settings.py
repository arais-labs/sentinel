"""Helpers for settings stored in the manager database."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.manager import ManagerSetting


async def get_manager_setting(db: AsyncSession, *, key: str) -> str | None:
    result = await db.execute(select(ManagerSetting).where(ManagerSetting.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else None


async def upsert_manager_setting(db: AsyncSession, *, key: str, value: str) -> None:
    result = await db.execute(select(ManagerSetting).where(ManagerSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        db.add(ManagerSetting(key=key, value=value))
    else:
        row.value = value
    await db.commit()


async def delete_manager_setting(db: AsyncSession, *, key: str) -> None:
    result = await db.execute(select(ManagerSetting).where(ManagerSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        return
    await db.delete(row)
    await db.commit()
