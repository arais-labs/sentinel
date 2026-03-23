from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.memory import MemoryRepository, MemoryService
from app.services.memory.system import SYSTEM_MEMORY_SPECS
from app.services.onboarding.onboarding_defaults import (
    DEFAULT_AGENT_IDENTITY_MEMORY,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_USER_PROFILE_MEMORY,
    build_system_prompt,
)
from app.services.settings.system_settings import get_system_setting, upsert_system_setting

ONBOARDING_COMPLETED_KEY_PREFIX = "onboarding_completed:"


class OnboardingService:
    def __init__(self) -> None:
        self._memory_service = MemoryService(MemoryRepository())

    async def is_completed(self, db: AsyncSession, *, user_id: str) -> bool:
        key = f"{ONBOARDING_COMPLETED_KEY_PREFIX}{user_id}"
        return (await get_system_setting(db, key=key)) is not None

    async def complete(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        agent_name: str | None,
        agent_role: str | None,
        agent_personality: str | None,
    ) -> None:
        prompt = build_system_prompt(
            agent_name=agent_name,
            agent_role=agent_role,
            agent_personality=agent_personality,
        )
        prompt = self._with_default(prompt, DEFAULT_SYSTEM_PROMPT)
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
        default_contents = {
            "agent_identity": DEFAULT_AGENT_IDENTITY_MEMORY,
            "user_profile": DEFAULT_USER_PROFILE_MEMORY,
        }

        for spec in SYSTEM_MEMORY_SPECS:
            await self._memory_service.upsert_system_memory(
                db,
                system_key=spec.key,
                title=spec.title,
                content=default_contents[spec.key],
                importance=spec.importance,
                metadata={},
            )
