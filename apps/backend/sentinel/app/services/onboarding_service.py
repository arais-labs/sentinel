from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Memory
from app.services.onboarding_defaults import (
    DEFAULT_AGENT_IDENTITY_MEMORY,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_USER_PROFILE_MEMORY,
)
from app.services.system_settings import get_system_setting, upsert_system_setting

ONBOARDING_COMPLETED_KEY_PREFIX = "onboarding_completed:"


@dataclass(frozen=True, slots=True)
class OnboardingDefaults:
    araios_runtime_url: str | None


class OnboardingService:
    async def is_completed(self, db: AsyncSession, *, user_id: str) -> bool:
        key = f"{ONBOARDING_COMPLETED_KEY_PREFIX}{user_id}"
        return (await get_system_setting(db, key=key)) is not None

    def get_defaults(self) -> OnboardingDefaults:
        return OnboardingDefaults(araios_runtime_url=settings.araios_url)

    async def complete(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        system_prompt: str | None,
    ) -> None:
        prompt = self._with_default(system_prompt, DEFAULT_SYSTEM_PROMPT)
        settings.default_system_prompt = prompt
        await upsert_system_setting(db, key="default_system_prompt", value=prompt)
        await upsert_system_setting(
            db,
            key=f"{ONBOARDING_COMPLETED_KEY_PREFIX}{user_id}",
            value=datetime.now(UTC).isoformat(),
        )
        await self._ensure_default_system_memories(db)

    @staticmethod
    def _with_default(value: str | None, default: str) -> str:
        trimmed = (value or "").strip()
        return trimmed or default

    async def _ensure_default_system_memories(self, db: AsyncSession) -> None:
        created = False
        defaults: list[tuple[str, str, int]] = [
            ("Agent Identity", DEFAULT_AGENT_IDENTITY_MEMORY, 100),
            ("User Profile", DEFAULT_USER_PROFILE_MEMORY, 90),
        ]
        for title, content, importance in defaults:
            existing = await self._core_memory_by_title(db, title=title)
            if existing is not None:
                continue
            db.add(
                Memory(
                    title=title,
                    content=content,
                    category="core",
                    importance=importance,
                    pinned=True,
                    metadata_json={},
                )
            )
            created = True

        if created:
            await db.commit()

    @staticmethod
    async def _core_memory_by_title(db: AsyncSession, *, title: str) -> Memory | None:
        result = await db.execute(
            select(Memory).where(
                Memory.title == title,
                Memory.category == "core",
                Memory.parent_id.is_(None),
            )
        )
        return result.scalars().first()
