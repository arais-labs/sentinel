from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, is_desktop_app, settings
from app.models.system import SystemSetting
from app.services.llm.ids import ProviderChoice, parse_provider_choice
from app.services.llm.providers.gemini_oauth import GeminiOAuthCredentials
from app.services.settings.system_settings import (
    delete_system_setting,
    upsert_system_setting,
)


@dataclass(frozen=True, slots=True)
class ProviderAuthStatus:
    configured: bool
    auth_method: str | None
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
class DesktopCodexOauthImportResult:
    masked_key: str


class SettingsService:
    PERSISTED_SETTINGS: tuple[str, ...] = (
        "anthropic_api_key",
        "anthropic_oauth_token",
        "openai_api_key",
        "openai_oauth_token",
        "gemini_api_key",
        "gemini_oauth_credentials",
        "primary_provider",
        "default_system_prompt",
        "telegram_bot_token",
        "telegram_owner_user_id",
        "telegram_owner_chat_id",
        "telegram_owner_telegram_user_id",
        "telegram_pairing_code_hash",
        "telegram_pairing_code_expires_at",
    )

    async def build_instance_settings(self, db: AsyncSession) -> Settings:
        instance_settings = settings.model_copy(deep=True)
        result = await db.execute(
            select(SystemSetting).where(SystemSetting.key.in_(self.PERSISTED_SETTINGS))
        )
        for row in result.scalars().all():
            if hasattr(instance_settings, row.key):
                setattr(instance_settings, row.key, row.value)
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

    def get_desktop_codex_oauth_status(self, *, auth_path: Path | None = None) -> DesktopCodexOauthStatus:
        enabled = is_desktop_app()
        return DesktopCodexOauthStatus(
            enabled=enabled,
            auth_file_found=enabled and (auth_path or self._codex_auth_path()).is_file(),
        )

    async def import_desktop_codex_oauth_token(
        self,
        db: AsyncSession,
        *,
        auth_path: Path | None = None,
    ) -> DesktopCodexOauthImportResult:
        path = auth_path or self._codex_auth_path()
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Codex auth file was not found at ~/.codex/auth.json.") from exc
        except OSError as exc:
            raise HTTPException(status_code=422, detail="Codex auth file could not be read.") from exc

        token = self._extract_codex_access_token(raw)
        await upsert_system_setting(db, key="openai_oauth_token", value=token)
        return DesktopCodexOauthImportResult(masked_key=self._mask_secret(token) or "****")

    def get_api_keys_status(self, instance_settings: Settings | None = None) -> ApiKeysStatus:
        settings_source = instance_settings or settings
        anthropic_key = settings_source.anthropic_api_key
        anthropic_oauth = settings_source.anthropic_oauth_token
        openai_key = settings_source.openai_api_key
        openai_oauth = settings_source.openai_oauth_token
        gemini_key = settings_source.gemini_api_key
        gemini_oauth = settings_source.gemini_oauth_credentials
        primary_provider = parse_provider_choice(settings_source.primary_provider) or ProviderChoice.ANTHROPIC

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
                    configured=bool(gemini_key or gemini_oauth),
                    auth_method="oauth" if gemini_oauth else ("api_key" if gemini_key else None),
                    masked_key=self._mask_gemini_secret(gemini_oauth or gemini_key),
                ),
            },
        )

    async def delete_api_keys(self, db: AsyncSession, *, provider: ProviderChoice) -> None:
        if provider == ProviderChoice.ANTHROPIC:
            await delete_system_setting(db, key="anthropic_api_key")
            await delete_system_setting(db, key="anthropic_oauth_token")
            return
        if provider == ProviderChoice.OPENAI:
            await delete_system_setting(db, key="openai_api_key")
            await delete_system_setting(db, key="openai_oauth_token")
            return
        if provider == ProviderChoice.GEMINI:
            await delete_system_setting(db, key="gemini_api_key")
            await delete_system_setting(db, key="gemini_oauth_credentials")

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
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="Codex auth file is not valid JSON.") from exc

        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Codex auth file must contain a JSON object.")

        token = SettingsService._find_codex_access_token(payload)
        if token is None:
            raise HTTPException(
                status_code=422,
                detail="Codex auth file does not contain an access_token.",
            )
        return token

    @staticmethod
    def _find_codex_access_token(payload: dict[str, Any]) -> str | None:
        priority_paths = (
            ("tokens", "access_token"),
            ("tokens", "accessToken"),
            ("auth", "access_token"),
            ("auth", "accessToken"),
            ("oauth", "access_token"),
            ("oauth", "accessToken"),
            ("access_token",),
            ("accessToken",),
            ("OPENAI_OAUTH_TOKEN",),
        )
        for path in priority_paths:
            current: Any = payload
            for key in path:
                if not isinstance(current, dict):
                    current = None
                    break
                current = current.get(key)
            token = SettingsService._strip_or_none(current if isinstance(current, str) else None)
            if token is not None:
                return token

        return SettingsService._find_nested_access_token(payload)

    @staticmethod
    def _find_nested_access_token(value: Any) -> str | None:
        if isinstance(value, dict):
            for key in ("access_token", "accessToken"):
                token = SettingsService._strip_or_none(value.get(key) if isinstance(value.get(key), str) else None)
                if token is not None:
                    return token
            for child in value.values():
                token = SettingsService._find_nested_access_token(child)
                if token is not None:
                    return token
        if isinstance(value, list):
            for child in value:
                token = SettingsService._find_nested_access_token(child)
                if token is not None:
                    return token
        return None
