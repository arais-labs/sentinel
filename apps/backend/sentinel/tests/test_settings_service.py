from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.config import settings
from app.models.system import SystemSetting
from app.services.llm.factory import _build_enabled_providers
from app.services.llm.ids import ProviderChoice
from app.services.llm.providers.gemini_oauth import GeminiOAuthProvider
from app.services.settings.settings_service import SettingsService
from tests.fake_db import FakeDB


class _ScalarResult:
    def __init__(self, rows: list[SystemSetting]) -> None:
        self._rows = rows

    def all(self) -> list[SystemSetting]:
        return self._rows


class _ExecuteResult:
    def __init__(self, rows: list[SystemSetting]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._rows)


class _FakeSettingsDb:
    def __init__(self, rows: list[SystemSetting]) -> None:
        self._rows = rows

    async def execute(self, _stmt):
        return _ExecuteResult(self._rows)


@pytest.mark.asyncio
async def test_build_instance_settings_restores_provider_keys_without_global_mutation() -> None:
    service = SettingsService()
    old_values = (
        settings.openai_api_key,
        settings.primary_provider,
    )
    try:
        settings.openai_api_key = None
        settings.primary_provider = ProviderChoice.ANTHROPIC
        instance_settings = await service.build_instance_settings(
            _FakeSettingsDb(
                [
                    SystemSetting(key="openai_api_key", value="sk-test"),
                    SystemSetting(key="primary_provider", value="openai"),
                ]
            )
        )
        assert instance_settings.openai_api_key == "sk-test"
        assert instance_settings.primary_provider == "openai"
        assert settings.openai_api_key is None
        assert settings.primary_provider == ProviderChoice.ANTHROPIC
    finally:
        settings.openai_api_key, settings.primary_provider = old_values


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


def test_extract_codex_access_token_from_cli_auth_json() -> None:
    token = SettingsService._extract_codex_access_token(
        json.dumps(
            {
                "tokens": {
                    "id_token": "not-this-token",
                    "access_token": "codex-access-token",
                    "refresh_token": "refresh-token",
                }
            }
        )
    )

    assert token == "codex-access-token"


def test_extract_codex_access_token_rejects_missing_token() -> None:
    with pytest.raises(HTTPException) as exc:
        SettingsService._extract_codex_access_token(json.dumps({"tokens": {"id_token": "id-token"}}))

    assert exc.value.status_code == 422
    assert "access_token" in str(exc.value.detail)


def test_desktop_codex_oauth_status_uses_app_env_and_auth_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"tokens":{"access_token":"codex-access-token"}}', encoding="utf-8")
    monkeypatch.setattr(settings, "app_env", "desktop")

    status = SettingsService().get_desktop_codex_oauth_status(auth_path=auth_path)

    assert status.enabled is True
    assert status.auth_file_found is True


@pytest.mark.asyncio
async def test_import_desktop_codex_oauth_token_persists_openai_oauth(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"tokens":{"access_token":"codex-access-token"}}', encoding="utf-8")
    fake_db = FakeDB(seed_auth=False)

    result = await SettingsService().import_desktop_codex_oauth_token(fake_db, auth_path=auth_path)

    assert result.masked_key == "code...oken"
    persisted = next(
        row.value
        for row in fake_db.storage[SystemSetting]
        if row.key == "openai_oauth_token"
    )
    assert persisted == "codex-access-token"
