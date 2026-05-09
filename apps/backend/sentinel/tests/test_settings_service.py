from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.config import settings
from app.services.llm.factory import _build_enabled_providers
from app.services.llm.ids import ProviderChoice
from app.services.llm.providers.gemini_oauth import GeminiOAuthProvider
from app.services.settings.settings_service import SettingsService


@pytest.mark.asyncio
async def test_set_api_keys_normalizes_gemini_oauth_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    persisted: list[tuple[str, str]] = []

    async def _fake_upsert(_db, *, key: str, value: str) -> None:
        persisted.append((key, value))

    monkeypatch.setattr(
        "app.services.settings.settings_service.upsert_system_setting",
        _fake_upsert,
    )

    service = SettingsService()
    await service.set_api_keys(
        None,
        anthropic_api_key=None,
        anthropic_oauth_token=None,
        openai_api_key=None,
        openai_oauth_token=None,
        gemini_api_key=None,
        gemini_oauth_credentials=json.dumps(
            {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
            }
        ),
    )

    assert (
        "gemini_oauth_credentials",
        '{"access_token":"access-token","refresh_token":"refresh-token","token_type":"Bearer","client_id":"test-client-id","client_secret":"test-client-secret"}',
    ) in persisted


@pytest.mark.asyncio
async def test_set_api_keys_rejects_gemini_oauth_without_refresh_token() -> None:
    service = SettingsService()

    with pytest.raises(HTTPException) as exc:
        await service.set_api_keys(
            None,
            anthropic_api_key=None,
            anthropic_oauth_token=None,
            openai_api_key=None,
            openai_oauth_token=None,
            gemini_api_key=None,
            gemini_oauth_credentials='{"access_token":"short-lived"}',
        )

    assert exc.value.status_code == 422
    assert "refresh_token" in str(exc.value.detail)


def test_get_api_keys_status_marks_gemini_oauth() -> None:
    service = SettingsService()
    old_oauth = settings.gemini_oauth_credentials
    old_api_key = settings.gemini_api_key
    try:
        settings.gemini_api_key = None
        settings.gemini_oauth_credentials = (
            '{"refresh_token":"refresh-token","token_type":"Bearer",'
            '"client_id":"test-client-id","client_secret":"test-client-secret"}'
        )
        status = service.get_api_keys_status()
        gemini = status.providers[ProviderChoice.GEMINI]
        assert gemini.configured is True
        assert gemini.auth_method == "oauth"
        assert gemini.masked_key == "refr...oken"
    finally:
        settings.gemini_oauth_credentials = old_oauth
        settings.gemini_api_key = old_api_key


def test_build_enabled_providers_prefers_gemini_oauth() -> None:
    old_values = (
        settings.anthropic_api_key,
        settings.anthropic_oauth_token,
        settings.openai_api_key,
        settings.openai_oauth_token,
        settings.gemini_api_key,
        settings.gemini_oauth_credentials,
    )
    try:
        settings.anthropic_api_key = None
        settings.anthropic_oauth_token = None
        settings.openai_api_key = None
        settings.openai_oauth_token = None
        settings.gemini_api_key = "AIza-test"
        settings.gemini_oauth_credentials = (
            '{"refresh_token":"refresh-token","token_type":"Bearer",'
            '"client_id":"test-client-id","client_secret":"test-client-secret"}'
        )

        providers, _ = _build_enabled_providers(settings)

        assert isinstance(providers[ProviderChoice.GEMINI], GeminiOAuthProvider)
    finally:
        (
            settings.anthropic_api_key,
            settings.anthropic_oauth_token,
            settings.openai_api_key,
            settings.openai_oauth_token,
            settings.gemini_api_key,
            settings.gemini_oauth_credentials,
        ) = old_values
