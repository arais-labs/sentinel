from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import sentral.llm.claude_credentials as claude_credentials_module
from app.config import Settings, settings
from app.models.system import SystemSetting
import sentral.llm.antigravity_credentials as antigravity_credentials_module
from sentral.llm.codex_credentials import extract_codex_access_token, read_codex_access_token
from sentral.llm.ids import ProviderChoice, parse_provider_choice
from sentral.llm.providers.gemini_oauth import GeminiOAuthCredentials
from app.services.settings.system_settings import (
    delete_system_setting,
    upsert_system_setting,
)


@dataclass(frozen=True, slots=True)
class ProviderAuthStatus:
    configured: bool
    auth_method: str | None
    auth_source: str | None
    masked_key: str | None


@dataclass(frozen=True, slots=True)
class ApiKeysStatus:
    primary_provider: ProviderChoice
    providers: dict[ProviderChoice, ProviderAuthStatus]


@dataclass(frozen=True, slots=True)
class DesktopCodexOauthStatus:
    enabled: bool
    auth_file_found: bool


@dataclass(frozen=True, slots=True)
class DesktopOauthConnectionResult:
    masked_key: str


class SettingsService:
    PERSISTED_SETTINGS: tuple[str, ...] = (
        "ollama_base_url",
        "ollama_api_key",
        "ollama_model",
        "anthropic_api_key",
        "anthropic_oauth_token",
        "anthropic_oauth_source",
        "openai_api_key",
        "openai_oauth_token",
        "openai_oauth_source",
        "gemini_api_key",
        "gemini_oauth_credentials",
        "gemini_oauth_source",
        "primary_provider",
        "default_system_prompt",
        "telegram_bot_token",
        "telegram_owner_user_id",
        "telegram_owner_chat_id",
        "telegram_owner_telegram_user_id",
    )

    async def build_instance_settings(self, db: AsyncSession) -> Settings:
        instance_settings = settings.model_copy(deep=True)
        result = await db.execute(
            select(SystemSetting).where(SystemSetting.key.in_(self.PERSISTED_SETTINGS))
        )
        for row in result.scalars().all():
            if hasattr(instance_settings, row.key):
                setattr(instance_settings, row.key, row.value)
        await self._hydrate_cli_oauth(instance_settings)
        return instance_settings

    async def set_api_keys(
        self,
        db: AsyncSession,
        *,
        anthropic_api_key: str | None,
        anthropic_oauth_token: str | None,
        openai_api_key: str | None,
        openai_oauth_token: str | None,
        gemini_api_key: str | None,
        gemini_oauth_credentials: str | None,
    ) -> None:
        await self._persist_if_present(
            db,
            setting_key="anthropic_api_key",
            value=anthropic_api_key,
        )
        await self._persist_if_present(
            db,
            setting_key="anthropic_oauth_token",
            value=anthropic_oauth_token,
        )
        await self._persist_if_present(
            db,
            setting_key="openai_api_key",
            value=openai_api_key,
        )
        await self._persist_if_present(
            db,
            setting_key="openai_oauth_token",
            value=openai_oauth_token,
        )
        await self._persist_if_present(
            db,
            setting_key="gemini_api_key",
            value=gemini_api_key,
        )
        normalized_gemini_oauth = self._normalize_gemini_oauth_credentials(gemini_oauth_credentials)
        await self._persist_if_present(
            db,
            setting_key="gemini_oauth_credentials",
            value=normalized_gemini_oauth,
        )
        await self._select_manual_source(
            db,
            provider="anthropic",
            api_key=anthropic_api_key,
            oauth_credential=anthropic_oauth_token,
        )
        await self._select_manual_source(
            db,
            provider="openai",
            api_key=openai_api_key,
            oauth_credential=openai_oauth_token,
        )
        await self._select_manual_source(
            db,
            provider="gemini",
            api_key=gemini_api_key,
            oauth_credential=normalized_gemini_oauth,
        )

    async def set_ollama(self, db: AsyncSession, *, base_url: str, model: str, api_key: str | None):
        # URL and credential change atomically: no request can pair a new host with an old key.
        values = {
            "ollama_base_url": base_url,
            "ollama_model": model,
            "ollama_api_key": api_key or "",
        }
        rows = (
            (await db.execute(select(SystemSetting).where(SystemSetting.key.in_(values))))
            .scalars()
            .all()
        )
        existing = {row.key: row for row in rows}
        for key, value in values.items():
            if key in existing:
                existing[key].value = value
            else:
                db.add(SystemSetting(key=key, value=value))
        await db.commit()

    async def connect_desktop_claude_oauth(self, db: AsyncSession) -> DesktopOauthConnectionResult:

        try:
            token = await claude_credentials_module.read_claude_access_token()
        except (OSError, ValueError, TimeoutError) as exc:
            raise HTTPException(
                status_code=422,
                detail="Could not read Claude CLI credentials. Sign in to Claude CLI and try again.",
            ) from exc
        if not token:
            raise HTTPException(
                status_code=404,
                detail="No Claude CLI login found. Run claude auth login, then try again.",
            )
        await self._enable_cli_oauth(db, provider="anthropic")
        return DesktopOauthConnectionResult(masked_key=self._mask_secret(token) or "****")

    async def connect_desktop_gemini_oauth(self, db: AsyncSession) -> DesktopOauthConnectionResult:

        try:
            credentials = await antigravity_credentials_module.read_antigravity_credentials()
        except (OSError, ValueError, TimeoutError) as exc:
            raise HTTPException(
                status_code=422,
                detail="Could not read Antigravity OAuth. Sign in with agy on this Mac and retry, "
                "or paste an exported Antigravity OAuth credential bundle.",
            ) from exc
        if credentials is None:
            raise HTTPException(
                status_code=404,
                detail="No Antigravity login found. Run agy and sign in with Google, then try again.",
            )
        await self._enable_cli_oauth(db, provider="gemini")
        return DesktopOauthConnectionResult(masked_key=credentials.mask_secret() or "****")

    def get_desktop_codex_oauth_status(
        self, *, auth_path: Path | None = None
    ) -> DesktopCodexOauthStatus:
        return DesktopCodexOauthStatus(
            enabled=True,
            auth_file_found=(auth_path or self._codex_auth_path()).is_file(),
        )

    async def connect_desktop_codex_oauth(
        self,
        db: AsyncSession,
        *,
        auth_path: Path | None = None,
    ) -> DesktopOauthConnectionResult:
        path = auth_path or self._codex_auth_path()
        try:
            token = await read_codex_access_token(path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=422, detail="Codex auth file could not be read."
            ) from exc

        if not token:
            raise HTTPException(
                status_code=404, detail="Codex auth file was not found at ~/.codex/auth.json."
            )
        await self._enable_cli_oauth(db, provider="openai")
        return DesktopOauthConnectionResult(masked_key=self._mask_secret(token) or "****")

    def get_api_keys_status(self, instance_settings: Settings | None = None) -> ApiKeysStatus:
        settings_source = instance_settings or settings
        anthropic_key = settings_source.anthropic_api_key
        anthropic_oauth = settings_source.anthropic_oauth_token
        openai_key = settings_source.openai_api_key
        openai_oauth = settings_source.openai_oauth_token
        gemini_key = settings_source.gemini_api_key
        gemini_oauth = settings_source.gemini_oauth_credentials
        anthropic_source = settings_source.anthropic_oauth_source
        openai_source = settings_source.openai_oauth_source
        gemini_source = settings_source.gemini_oauth_source
        primary_provider = (
            parse_provider_choice(settings_source.primary_provider) or ProviderChoice.ANTHROPIC
        )

        return ApiKeysStatus(
            primary_provider=primary_provider,
            providers={
                ProviderChoice.OLLAMA: ProviderAuthStatus(
                    configured=bool(
                        settings_source.ollama_base_url and settings_source.ollama_model
                    ),
                    auth_method="api_key" if settings_source.ollama_api_key else None,
                    auth_source="manual",
                    masked_key=self._mask_secret(settings_source.ollama_api_key),
                ),
                ProviderChoice.ANTHROPIC: ProviderAuthStatus(
                    configured=bool(anthropic_key or anthropic_oauth),
                    auth_method=(
                        "oauth"
                        if anthropic_oauth or anthropic_source == "cli"
                        else ("api_key" if anthropic_key else None)
                    ),
                    auth_source=(
                        (anthropic_source or "manual")
                        if anthropic_oauth or anthropic_source == "cli"
                        else None
                    ),
                    masked_key=(
                        "Claude CLI · Auto-sync"
                        if anthropic_source == "cli"
                        else self._mask_secret(anthropic_oauth or anthropic_key)
                    ),
                ),
                ProviderChoice.OPENAI: ProviderAuthStatus(
                    configured=bool(openai_key or openai_oauth),
                    auth_method=(
                        "oauth"
                        if openai_oauth or openai_source == "cli"
                        else ("api_key" if openai_key else None)
                    ),
                    auth_source=(
                        (openai_source or "manual")
                        if openai_oauth or openai_source == "cli"
                        else None
                    ),
                    masked_key=(
                        "Codex CLI · Auto-sync"
                        if openai_source == "cli"
                        else self._mask_secret(openai_oauth or openai_key)
                    ),
                ),
                ProviderChoice.GEMINI: ProviderAuthStatus(
                    configured=bool(gemini_key or gemini_oauth),
                    auth_method=(
                        "oauth"
                        if gemini_oauth or gemini_source == "cli"
                        else ("api_key" if gemini_key else None)
                    ),
                    auth_source=(
                        (gemini_source or "manual")
                        if gemini_oauth or gemini_source == "cli"
                        else None
                    ),
                    masked_key=(
                        "Antigravity · Auto-sync"
                        if gemini_source == "cli"
                        else self._mask_gemini_secret(gemini_oauth or gemini_key)
                    ),
                ),
            },
        )

    async def delete_api_keys(self, db: AsyncSession, *, provider: ProviderChoice) -> None:
        if provider == ProviderChoice.OLLAMA:
            for key in ("ollama_base_url", "ollama_api_key", "ollama_model"):
                await delete_system_setting(db, key=key)
            return
        if provider == ProviderChoice.ANTHROPIC:
            await delete_system_setting(db, key="anthropic_api_key")
            await delete_system_setting(db, key="anthropic_oauth_token")
            await delete_system_setting(db, key="anthropic_oauth_source")
            return
        if provider == ProviderChoice.OPENAI:
            await delete_system_setting(db, key="openai_api_key")
            await delete_system_setting(db, key="openai_oauth_token")
            await delete_system_setting(db, key="openai_oauth_source")
            return
        if provider == ProviderChoice.GEMINI:
            await delete_system_setting(db, key="gemini_api_key")
            await delete_system_setting(db, key="gemini_oauth_credentials")
            await delete_system_setting(db, key="gemini_oauth_source")

    async def _hydrate_cli_oauth(self, instance_settings: Settings) -> None:
        if instance_settings.anthropic_oauth_source == "cli":
            try:
                instance_settings.anthropic_oauth_token = (
                    await claude_credentials_module.read_claude_access_token()
                )
            except (OSError, ValueError, TimeoutError):
                instance_settings.anthropic_oauth_token = None
        if instance_settings.openai_oauth_source == "cli":
            try:
                instance_settings.openai_oauth_token = await read_codex_access_token()
            except (OSError, ValueError, TimeoutError):
                instance_settings.openai_oauth_token = None
        if instance_settings.gemini_oauth_source == "cli":
            try:
                credentials = await antigravity_credentials_module.read_antigravity_credentials()
                instance_settings.gemini_oauth_credentials = (
                    credentials.as_json() if credentials is not None else None
                )
            except (OSError, ValueError, TimeoutError):
                instance_settings.gemini_oauth_credentials = None

    @staticmethod
    async def _enable_cli_oauth(db: AsyncSession, *, provider: str) -> None:
        await upsert_system_setting(db, key=f"{provider}_oauth_source", value="cli")
        await delete_system_setting(db, key=f"{provider}_api_key")

    @staticmethod
    async def _select_manual_source(
        db: AsyncSession,
        *,
        provider: str,
        api_key: str | None,
        oauth_credential: str | None,
    ) -> None:
        if SettingsService._strip_or_none(oauth_credential) is not None:
            await upsert_system_setting(db, key=f"{provider}_oauth_source", value="manual")
            await delete_system_setting(db, key=f"{provider}_api_key")
        elif SettingsService._strip_or_none(api_key) is not None:
            await delete_system_setting(db, key=f"{provider}_oauth_source")
            oauth_key = (
                "gemini_oauth_credentials" if provider == "gemini" else f"{provider}_oauth_token"
            )
            await delete_system_setting(db, key=oauth_key)

    async def set_primary_provider(
        self,
        db: AsyncSession,
        *,
        provider: ProviderChoice,
    ) -> None:
        await upsert_system_setting(db, key="primary_provider", value=provider.value)

    @staticmethod
    async def _persist_if_present(
        db: AsyncSession,
        *,
        setting_key: str,
        value: str | None,
    ) -> None:
        normalized = SettingsService._strip_or_none(value)
        if normalized is None:
            return
        await upsert_system_setting(db, key=setting_key, value=normalized)

    @staticmethod
    def _strip_or_none(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _normalize_gemini_oauth_credentials(value: str | None) -> str | None:
        normalized = SettingsService._strip_or_none(value)
        if normalized is None:
            return None
        try:
            credentials = GeminiOAuthCredentials.parse_input(normalized)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return credentials.as_json()

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
    def _mask_secret(value: str | None) -> str | None:
        if not value:
            return None
        if len(value) <= 8:
            return "****"
        return value[:4] + "..." + value[-4:]

    @staticmethod
    def _mask_gemini_secret(value: str | None) -> str | None:
        if not value:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.startswith("{"):
            try:
                return GeminiOAuthCredentials.parse_input(normalized).mask_secret()
            except ValueError:
                return SettingsService._mask_secret(normalized)
        return SettingsService._mask_secret(normalized)

    @staticmethod
    def _codex_auth_path() -> Path:
        return Path.home() / ".codex" / "auth.json"

    @staticmethod
    def _extract_codex_access_token(raw: str) -> str:
        try:
            return extract_codex_access_token(raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
