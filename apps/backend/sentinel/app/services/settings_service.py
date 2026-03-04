from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.llm.ids import ProviderChoice, parse_provider_choice
from app.services.system_settings import (
    delete_system_setting,
    get_system_setting,
    upsert_system_setting,
)

ARAIOS_FRONTEND_URL_SETTING_KEY = "araios_frontend_url"
ARAIOS_BACKEND_URL_SETTING_KEY = "araios_backend_url"
ARAIOS_AGENT_API_KEY_SETTING_KEY = "araios_integration_agent_api_key"


@dataclass(frozen=True, slots=True)
class AraiOSIntegrationStatus:
    configured: bool
    araios_frontend_url: str | None
    araios_backend_url: str | None
    masked_agent_api_key: str | None


@dataclass(frozen=True, slots=True)
class ProviderAuthStatus:
    configured: bool
    auth_method: str | None
    masked_key: str | None


@dataclass(frozen=True, slots=True)
class ApiKeysStatus:
    primary_provider: ProviderChoice
    providers: dict[ProviderChoice, ProviderAuthStatus]


class SettingsService:
    async def get_araios_integration(self, db: AsyncSession) -> AraiOSIntegrationStatus:
        araios_frontend_url = await get_system_setting(db, key=ARAIOS_FRONTEND_URL_SETTING_KEY)
        araios_backend_url = await get_system_setting(db, key=ARAIOS_BACKEND_URL_SETTING_KEY)
        agent_api_key = await get_system_setting(db, key=ARAIOS_AGENT_API_KEY_SETTING_KEY)
        configured = bool(
            (araios_frontend_url or "").strip()
            and (araios_backend_url or "").strip()
            and (agent_api_key or "").strip()
        )
        return AraiOSIntegrationStatus(
            configured=configured,
            araios_frontend_url=araios_frontend_url,
            araios_backend_url=araios_backend_url,
            masked_agent_api_key=self._mask_secret(agent_api_key),
        )

    async def set_araios_integration(
        self,
        db: AsyncSession,
        *,
        enabled: bool,
        araios_frontend_url: str | None,
        araios_backend_url: str | None,
        agent_api_key: str | None,
    ) -> AraiOSIntegrationStatus:
        if not enabled:
            if araios_frontend_url is not None:
                resolved_araios_frontend_url = self._normalize_url(
                    araios_frontend_url,
                    missing_message="AraiOS frontend URL is required",
                    invalid_message="AraiOS frontend URL must be a valid http/https URL",
                )
                await upsert_system_setting(
                    db, key=ARAIOS_FRONTEND_URL_SETTING_KEY, value=resolved_araios_frontend_url
                )

            await delete_system_setting(db, key=ARAIOS_BACKEND_URL_SETTING_KEY)
            await delete_system_setting(db, key=ARAIOS_AGENT_API_KEY_SETTING_KEY)
            return await self.get_araios_integration(db)

        existing_araios_frontend_url = await get_system_setting(
            db, key=ARAIOS_FRONTEND_URL_SETTING_KEY
        )
        existing_araios_backend_url = await get_system_setting(
            db, key=ARAIOS_BACKEND_URL_SETTING_KEY
        )
        existing_agent_api_key = await get_system_setting(db, key=ARAIOS_AGENT_API_KEY_SETTING_KEY)

        resolved_araios_frontend_url = (
            self._normalize_url(
                araios_frontend_url,
                missing_message="AraiOS frontend URL is required",
                invalid_message="AraiOS frontend URL must be a valid http/https URL",
            )
            if araios_frontend_url is not None
            else self._normalize_url(
                existing_araios_frontend_url,
                missing_message="AraiOS frontend URL is required",
                invalid_message="AraiOS frontend URL must be a valid http/https URL",
            )
        )
        resolved_araios_backend_url = (
            self._normalize_url(
                araios_backend_url,
                missing_message="AraiOS backend URL is required",
                invalid_message="AraiOS backend URL must be a valid http/https URL",
            )
            if araios_backend_url is not None
            else self._normalize_url(
                existing_araios_backend_url,
                missing_message="AraiOS backend URL is required",
                invalid_message="AraiOS backend URL must be a valid http/https URL",
            )
        )
        resolved_agent_api_key = (
            self._strip_or_none(agent_api_key)
            if agent_api_key is not None
            else self._strip_or_none(existing_agent_api_key)
        )
        if not resolved_agent_api_key:
            raise HTTPException(status_code=422, detail="AraiOS agent API key is required")

        await upsert_system_setting(
            db, key=ARAIOS_FRONTEND_URL_SETTING_KEY, value=resolved_araios_frontend_url
        )
        await upsert_system_setting(
            db, key=ARAIOS_BACKEND_URL_SETTING_KEY, value=resolved_araios_backend_url
        )
        await upsert_system_setting(
            db, key=ARAIOS_AGENT_API_KEY_SETTING_KEY, value=resolved_agent_api_key
        )
        return AraiOSIntegrationStatus(
            configured=True,
            araios_frontend_url=resolved_araios_frontend_url,
            araios_backend_url=resolved_araios_backend_url,
            masked_agent_api_key=self._mask_secret(resolved_agent_api_key),
        )

    async def get_araios_runtime_credentials(self, db: AsyncSession) -> tuple[str, str]:
        backend_url = await get_system_setting(db, key=ARAIOS_BACKEND_URL_SETTING_KEY)
        agent_api_key = await get_system_setting(db, key=ARAIOS_AGENT_API_KEY_SETTING_KEY)
        normalized_backend_url = self._normalize_runtime_url(backend_url)
        normalized_agent_api_key = self._strip_or_none(agent_api_key)
        if not normalized_agent_api_key:
            raise ValueError("AraiOS integration is not configured: missing agent API key")
        return normalized_backend_url, normalized_agent_api_key

    async def set_api_keys(
        self,
        db: AsyncSession,
        *,
        anthropic_api_key: str | None,
        anthropic_oauth_token: str | None,
        openai_api_key: str | None,
        openai_oauth_token: str | None,
        gemini_api_key: str | None,
    ) -> None:
        await self._persist_if_present(
            db,
            setting_attr="anthropic_api_key",
            setting_key="anthropic_api_key",
            value=anthropic_api_key,
        )
        await self._persist_if_present(
            db,
            setting_attr="anthropic_oauth_token",
            setting_key="anthropic_oauth_token",
            value=anthropic_oauth_token,
        )
        await self._persist_if_present(
            db,
            setting_attr="openai_api_key",
            setting_key="openai_api_key",
            value=openai_api_key,
        )
        await self._persist_if_present(
            db,
            setting_attr="openai_oauth_token",
            setting_key="openai_oauth_token",
            value=openai_oauth_token,
        )
        await self._persist_if_present(
            db,
            setting_attr="gemini_api_key",
            setting_key="gemini_api_key",
            value=gemini_api_key,
        )

    def get_api_keys_status(self) -> ApiKeysStatus:
        anthropic_key = settings.anthropic_api_key
        anthropic_oauth = settings.anthropic_oauth_token
        openai_key = settings.openai_api_key
        openai_oauth = settings.openai_oauth_token
        gemini_key = settings.gemini_api_key
        primary_provider = parse_provider_choice(settings.primary_provider) or ProviderChoice.ANTHROPIC

        return ApiKeysStatus(
            primary_provider=primary_provider,
            providers={
                ProviderChoice.ANTHROPIC: ProviderAuthStatus(
                    configured=bool(anthropic_key or anthropic_oauth),
                    auth_method="oauth" if anthropic_oauth else ("api_key" if anthropic_key else None),
                    masked_key=self._mask_secret(anthropic_oauth or anthropic_key),
                ),
                ProviderChoice.OPENAI: ProviderAuthStatus(
                    configured=bool(openai_key or openai_oauth),
                    auth_method="oauth" if openai_oauth else ("api_key" if openai_key else None),
                    masked_key=self._mask_secret(openai_oauth or openai_key),
                ),
                ProviderChoice.GEMINI: ProviderAuthStatus(
                    configured=bool(gemini_key),
                    auth_method="api_key" if gemini_key else None,
                    masked_key=self._mask_secret(gemini_key),
                ),
            },
        )

    async def delete_api_keys(self, db: AsyncSession, *, provider: ProviderChoice) -> None:
        if provider == ProviderChoice.ANTHROPIC:
            settings.anthropic_api_key = None
            settings.anthropic_oauth_token = None
            await delete_system_setting(db, key="anthropic_api_key")
            await delete_system_setting(db, key="anthropic_oauth_token")
            return
        if provider == ProviderChoice.OPENAI:
            settings.openai_api_key = None
            settings.openai_oauth_token = None
            await delete_system_setting(db, key="openai_api_key")
            await delete_system_setting(db, key="openai_oauth_token")
            return
        if provider == ProviderChoice.GEMINI:
            settings.gemini_api_key = None
            await delete_system_setting(db, key="gemini_api_key")

    async def set_primary_provider(
        self,
        db: AsyncSession,
        *,
        provider: ProviderChoice,
    ) -> None:
        settings.primary_provider = provider.value
        await upsert_system_setting(db, key="primary_provider", value=provider.value)

    @staticmethod
    async def _persist_if_present(
        db: AsyncSession,
        *,
        setting_attr: str,
        setting_key: str,
        value: str | None,
    ) -> None:
        normalized = SettingsService._strip_or_none(value)
        if normalized is None:
            return
        setattr(settings, setting_attr, normalized)
        await upsert_system_setting(db, key=setting_key, value=normalized)

    @staticmethod
    def _strip_or_none(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _normalize_url(
        value: str | None,
        *,
        missing_message: str,
        invalid_message: str,
    ) -> str:
        normalized = (value or "").strip().rstrip("/")
        if not normalized:
            raise HTTPException(status_code=422, detail=missing_message)
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=422, detail=invalid_message)
        return normalized

    @staticmethod
    def _normalize_runtime_url(value: str | None) -> str:
        normalized = (value or "").strip().rstrip("/")
        if not normalized:
            raise ValueError("AraiOS integration is not configured: missing backend URL")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("AraiOS integration is not configured: backend URL is invalid")
        return normalized

    @staticmethod
    def _mask_secret(value: str | None) -> str | None:
        if not value:
            return None
        if len(value) <= 8:
            return "****"
        return value[:4] + "..." + value[-4:]
