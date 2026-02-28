from __future__ import annotations

from enum import IntEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSetting


class EstopLevel(IntEnum):
    NONE = 0
    TOOL_FREEZE = 1
    NETWORK_KILL = 2
    KILL_ALL = 3

    @classmethod
    def coerce(cls, value: int | str | None) -> "EstopLevel":
        if value is None:
            return cls.NONE
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return cls.NONE
            try:
                parsed = int(value)
            except ValueError:
                return cls.NONE
            value = parsed
        if not isinstance(value, int):
            return cls.NONE
        if value < int(cls.NONE) or value > int(cls.KILL_ALL):
            return cls.NONE
        return cls(value)


class EstopService:
    SETTING_KEY = "estop_level"
    LEGACY_ACTIVE_KEY = "estop_active"

    async def check_level(self, db: AsyncSession) -> EstopLevel:
        level_setting = await self._get_setting(db, self.SETTING_KEY)
        if level_setting is not None:
            return EstopLevel.coerce(level_setting.value)

        # Backward compatibility with old bool setting.
        legacy = await self._get_setting(db, self.LEGACY_ACTIVE_KEY)
        if legacy is not None and legacy.value.lower() == "true":
            return EstopLevel.TOOL_FREEZE
        return EstopLevel.NONE

    async def set_level(self, db: AsyncSession, level: EstopLevel) -> None:
        setting = await self._get_setting(db, self.SETTING_KEY)
        value = str(int(level))
        if setting is None:
            db.add(SystemSetting(key=self.SETTING_KEY, value=value))
        else:
            setting.value = value
        await db.commit()

    async def is_active(self, db: AsyncSession) -> bool:
        return (await self.check_level(db)) != EstopLevel.NONE

    async def enforce_tool(self, db: AsyncSession, tool_name: str, risk_level: str) -> None:
        _ = risk_level
        level = await self.check_level(db)
        if level == EstopLevel.NONE:
            return
        if level == EstopLevel.KILL_ALL:
            raise PermissionError("Emergency stop KILL_ALL blocks all operations")
        if level == EstopLevel.TOOL_FREEZE:
            raise PermissionError("Emergency stop TOOL_FREEZE blocks all tool execution")
        if level == EstopLevel.NETWORK_KILL:
            if tool_name == "http_request" or tool_name.startswith("browser_"):
                raise PermissionError(f"Emergency stop NETWORK_KILL blocks tool '{tool_name}'")
            raise PermissionError("Emergency stop NETWORK_KILL blocks tool execution")

    async def _get_setting(self, db: AsyncSession, key: str) -> SystemSetting | None:
        result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
        return result.scalars().first()
