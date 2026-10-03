from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import httpx
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import sentral.llm.claude_credentials as claude_credentials_module
from app.config import Settings, settings
from app.models.system import SystemSetting
import sentral.llm.antigravity_credentials as antigravity_credentials_module
from sentral.llm.codex_credentials import (
    extract_codex_access_token,
    read_codex_access_token,
)
from sentral.llm.ids import ProviderChoice, parse_provider_choice
from app.services.llm.routing import automatic_provider_order, provider_order
from sentral.llm.providers.gemini_oauth import GeminiOAuthCredentials
from app.services.settings.system_settings import (
    delete_system_setting,
    upsert_system_setting,
)

MODEL_TIERS = ("fast", "normal", "hard")
MODEL_NAMESPACES = ("anthropic", "openai", "codex", "gemini", "ollama")


def provider_model_namespace(config: Settings, provider: ProviderChoice) -> str:
    if provider == ProviderChoice.OPENAI and (
        config.openai_oauth_token or config.openai_oauth_source == "cli"
    ):
        return "codex"
    return provider.value


def selected_provider_models(config: Settings, provider: ProviderChoice) -> dict[str, str]:
    namespace = provider_model_namespace(config, provider)
    return {
        tier: getattr(config, f"tier_{tier}_{namespace}_model")
        or (config.ollama_model if provider == ProviderChoice.OLLAMA else "")
        or ""
        for tier in MODEL_TIERS
    }


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
    PERSISTED_SETTINGS: tuple[str, ...] = tuple(
        f"tier_{tier}_{provider}_model" for provider in MODEL_NAMESPACES for tier in MODEL_TIERS
    ) + (
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
        "provider_order",
        "automatic_providers",
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
                value = row.value
                if row.key in ("provider_order", "automatic_providers"):
                    value = [ProviderChoice(item) for item in json.loads(value)]
                setattr(instance_settings, row.key, value)
        await self._hydrate_cli_oauth(instance_settings)
        return instance_settings

    def get_provider_routing(self, config: Settings) -> dict:
        status = self.get_api_keys_status(config)
        order = provider_order(config)
        automatic = [
            provider
            for provider in automatic_provider_order(config)
            if status.providers[provider].configured
        ]
        return {
            "order": [
                provider.value
                for provider in [*automatic, *(item for item in order if item not in automatic)]
            ],
            "automatic": [provider.value for provider in automatic],
        }

    async def set_provider_routing(
        self, db: AsyncSession, *, order: list[ProviderChoice], automatic: list[ProviderChoice]
    ) -> None:
        config = await self.build_instance_settings(db)
        status = self.get_api_keys_status(config)
        if any(not status.providers[provider].configured for provider in automatic):
            raise HTTPException(
                status_code=409, detail="Configure providers before enabling automatic routing."
            )
        order = [
            *(provider for provider in order if provider in automatic),
            *(provider for provider in order if provider not in automatic),
        ]
        values = {
            "provider_order": json.dumps([provider.value for provider in order]),
            "automatic_providers": json.dumps([provider.value for provider in automatic]),
            "primary_provider": next(provider.value for provider in order if provider in automatic),
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

    async def get_provider_models(self, db: AsyncSession, provider: ProviderChoice) -> dict:
        config = await self.build_instance_settings(db)
        namespace = provider_model_namespace(config, provider)
        keys = {tier: f"tier_{tier}_{namespace}_model" for tier in MODEL_TIERS}
        rows = (
            (await db.execute(select(SystemSetting).where(SystemSetting.key.in_(keys.values()))))
            .scalars()
            .all()
        )
        saved = {row.key: row.value for row in rows}
        defaults = {
            tier: (
                (config.ollama_model or "")
                if provider == ProviderChoice.OLLAMA
                else getattr(settings, key)
            )
            for tier, key in keys.items()
        }
        return {
            "namespace": namespace,
            "defaults": defaults,
            "overrides": {tier: saved.get(key) for tier, key in keys.items()},
            "effective": selected_provider_models(config, provider),
        }

    async def set_provider_models(
        self,
        db: AsyncSession,
        provider: ProviderChoice,
        *,
        namespace: str,
        models: dict[str, str | None],
    ) -> None:
        config = await self.build_instance_settings(db)
        if namespace != provider_model_namespace(config, provider):
            raise HTTPException(
                409,
                "Provider connection changed. Reopen provider settings and try again.",
            )
        if provider == ProviderChoice.OLLAMA:
            from sentral.llm.providers.ollama import OllamaProvider
            from app.services.llm.ollama_models import endpoint_error

            if not config.ollama_base_url or not config.ollama_model:
                raise HTTPException(409, "Configure an Ollama server and default model first.")
            adapter = OllamaProvider(config.ollama_base_url, api_key=config.ollama_api_key)
            for model in dict.fromkeys(value for value in models.values() if value):
                try:
                    info = await adapter.model_info(model)
                except (httpx.HTTPError, ValueError) as exc:
                    raise HTTPException(502, endpoint_error(exc, "inspect")) from exc
                if "tools" not in info.get("capabilities", []):
                    raise HTTPException(422, "Selected Ollama models must support tools.")
        values = {f"tier_{tier}_{namespace}_model": value for tier, value in models.items()}
        rows = (
            (await db.execute(select(SystemSetting).where(SystemSetting.key.in_(values))))
            .scalars()
            .all()
        )
        existing = {row.key: row for row in rows}
        for key, value in values.items():
            if value is None:
                if key in existing:
                    await db.delete(existing[key])
            elif key in existing:
                existing[key].value = value
            else:
                db.add(SystemSetting(key=key, value=value))
        await db.commit()

    async def provider_model_options(self, db: AsyncSession, provider: ProviderChoice) -> dict:
        from sentral.llm.providers.anthropic import AnthropicProvider
        from sentral.llm.providers.openai import OpenAIProvider
        from sentral.llm.providers.codex import CodexProvider
        from sentral.llm.providers.gemini import GeminiProvider
        from sentral.llm.providers.ollama import OllamaProvider

        config = await self.build_instance_settings(db)
        namespace = provider_model_namespace(config, provider)
        message = None
        try:
            async with asyncio.timeout(10):
                if provider == ProviderChoice.OLLAMA:
                    if not config.ollama_base_url:
                        raise ValueError("Not connected")
                    models = await OllamaProvider(
                        config.ollama_base_url, api_key=config.ollama_api_key
                    ).discover_models()
                    choices = [
                        model["name"] for model in models if isinstance(model.get("name"), str)
                    ]
                elif provider == ProviderChoice.GEMINI and config.gemini_oauth_credentials:
                    # Code Assist quota identities are suggestions, not an API-key model catalog.
                    from app.services.settings.provider_usage import (
                        provider_usage_service,
                    )

                    usage = await provider_usage_service.get_usage("gemini", config)
                    choices = [
                        window.model_scope
                        for window in usage.windows
                        if window.model_scope and window.model_scope.startswith("gemini-")
                    ]
                    message = "Suggestions from reported model quotas; availability may vary."
                else:
                    adapters = {
                        ProviderChoice.ANTHROPIC: lambda token: AnthropicProvider(token),
                        ProviderChoice.OPENAI: lambda token: (
                            CodexProvider(token)
                            if namespace == "codex"
                            else OpenAIProvider(token, base_url=config.openai_base_url)
                        ),
                        ProviderChoice.GEMINI: lambda token: GeminiProvider(token),
                    }
                    credential = getattr(config, f"{provider.value}_oauth_token", None) or getattr(
                        config, f"{provider.value}_api_key", None
                    )
                    if not credential:
                        raise ValueError("Not connected")
                    choices = await adapters[provider](credential).list_model_ids()
                    if provider == ProviderChoice.OPENAI:
                        choices = [
                            model
                            for model in choices
                            if model.startswith(("gpt-", "o3", "o4"))
                            and not any(
                                kind in model
                                for kind in (
                                    "audio",
                                    "realtime",
                                    "image",
                                    "transcribe",
                                    "search",
                                )
                            )
                        ]
                    elif provider == ProviderChoice.GEMINI:
                        choices = [
                            model
                            for model in choices
                            if model.startswith("gemini-")
                            and not any(kind in model for kind in ("image", "tts"))
                        ]
            if not choices and message is None:
                message = "No model suggestions reported. You can still enter a model ID."
            return {
                "namespace": namespace,
                "models": sorted(set(choices)),
                "message": message,
            }
        except (
            httpx.HTTPError,
            TimeoutError,
            ValueError,
            RuntimeError,
            TypeError,
            AttributeError,
        ):
            # Never expose credential-bearing upstream error bodies in Settings.
            return {
                "namespace": namespace,
                "models": [],
                "message": "Model suggestions are unavailable. You can still enter a model ID.",
            }

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
        tier_keys = [f"tier_{tier}_ollama_model" for tier in MODEL_TIERS]
        rows = (
            (
                await db.execute(
                    select(SystemSetting).where(SystemSetting.key.in_([*values, *tier_keys]))
                )
            )
            .scalars()
            .all()
        )
        existing = {row.key: row for row in rows}
        old_endpoint = existing.get("ollama_base_url")
        if old_endpoint is not None and old_endpoint.value != base_url:
            # An installed model name on one server is not a selection on another.
            for key in tier_keys:
                if key in existing:
                    await db.delete(existing[key])
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
                status_code=404,
                detail="Codex auth file was not found at ~/.codex/auth.json.",
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
            for key in (
                "ollama_base_url",
                "ollama_api_key",
                "ollama_model",
                *(f"tier_{tier}_ollama_model" for tier in MODEL_TIERS),
            ):
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
        config = await self.build_instance_settings(db)
        if config.provider_order is not None:
            order = [provider, *(item for item in provider_order(config) if item != provider)]
            automatic = automatic_provider_order(config)
            if provider not in automatic:
                automatic.append(provider)
            await self.set_provider_routing(db, order=order, automatic=automatic)
        else:
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
