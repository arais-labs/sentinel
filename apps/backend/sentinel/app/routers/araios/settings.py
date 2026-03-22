"""AraiOS Settings router — async SQLAlchemy."""
from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth, require_admin
from app.models.system import SystemSetting

router = APIRouter(tags=["araios-settings"])


# ── Schemas ──


class SettingOut(BaseModel):
    key: str
    value: str
    updatedAt: str | None = None


class SettingBody(BaseModel):
    value: str = Field(..., description="Setting value")


class SettingsListResponse(BaseModel):
    settings: list[SettingOut]


# ── Routes ──


@router.get("", response_model=SettingsListResponse)
async def list_settings(
    _user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SystemSetting))
    rows = result.scalars().all()
    return SettingsListResponse(
        settings=[
            SettingOut(
                key=r.key,
                value=r.value,
                updatedAt=r.updated_at.isoformat() if r.updated_at else None,
            )
            for r in rows
        ]
    )


@router.put("/{key}", response_model=SettingOut)
async def set_setting(
    key: str,
    body: SettingBody,
    _admin: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalars().first()

    if not setting:
        setting = SystemSetting(key=key, value=body.value)
        db.add(setting)
    else:
        setting.value = body.value

    await db.commit()
    await db.refresh(setting)
    return SettingOut(
        key=setting.key,
        value=setting.value,
        updatedAt=setting.updated_at.isoformat() if setting.updated_at else None,
    )
