from collections.abc import AsyncGenerator
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, settings
from app.database.database import get_db_session
from app.services.llm.generic.base import LLMProvider
from app.services.onboarding_service import OnboardingService
from app.services.runtime_rebuild import RuntimeRebuildService
from app.services.settings_service import SettingsService


def get_settings() -> Settings:
    """Dependency accessor to keep settings wiring centralized."""
    return settings


def get_onboarding_service() -> OnboardingService:
    return OnboardingService()


def get_settings_service() -> SettingsService:
    return SettingsService()


def get_runtime_rebuild_service() -> RuntimeRebuildService:
    return RuntimeRebuildService()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


def get_llm_provider(request: Request) -> LLMProvider:
    """Typed accessor for runtime LLM provider stored on app state."""
    if not hasattr(request.app.state, "llm_provider"):
        raise RuntimeError("LLM provider is missing from app state; startup is incomplete")
    provider = cast(LLMProvider | None, request.app.state.llm_provider)
    if provider is None:
        raise RuntimeError("LLM provider is not configured; check provider credentials and runtime rebuild")
    return provider


def get_optional_llm_provider(request: Request) -> LLMProvider | None:
    """Typed accessor that allows routes to operate without an LLM provider."""
    return cast(LLMProvider | None, request.app.state.llm_provider)
