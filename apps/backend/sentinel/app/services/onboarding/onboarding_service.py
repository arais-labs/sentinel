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
    build_agent_identity_memory,
    build_system_prompt,
    build_user_profile_memory,
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
        user_name: str | None = None,
        user_context: str | None = None,
    ) -> None:
        prompt = build_system_prompt(
            agent_name=agent_name,
            agent_role=agent_role,
            agent_personality=agent_personality,
        )
        prompt = self._with_default(prompt, DEFAULT_SYSTEM_PROMPT)
        settings.default_system_prompt = prompt
        await upsert_system_setting(db, key="default_system_prompt", value=prompt)
        await self._ensure_system_memories(
            db,
            agent_identity=build_agent_identity_memory(
                agent_name=agent_name,
                agent_role=agent_role,
                agent_personality=agent_personality,
            ),
            user_profile=build_user_profile_memory(
                user_name=user_name,
                user_context=user_context,
            ),
        )
        await upsert_system_setting(
            db,
            key=f"{ONBOARDING_COMPLETED_KEY_PREFIX}{user_id}",
            value=datetime.now(UTC).isoformat(),
        )

    @staticmethod
    def _with_default(value: str | None, default: str) -> str:
        trimmed = (value or "").strip()
        return trimmed or default

    async def _ensure_system_memories(
        self,
        db: AsyncSession,
        *,
        agent_identity: str = DEFAULT_AGENT_IDENTITY_MEMORY,
        user_profile: str = DEFAULT_USER_PROFILE_MEMORY,
    ) -> None:
        default_contents = {
            "agent_identity": agent_identity,
            "user_profile": user_profile,
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
