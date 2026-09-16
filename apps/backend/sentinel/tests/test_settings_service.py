from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.config import settings
from app.models.system import SystemSetting
from app.services.llm.factory import _build_enabled_providers
from app.services.llm.live_credentials import LiveCredentialProvider
from sentral.llm.codex_credentials import read_codex_access_token
from sentral.llm.ids import ProviderChoice
from sentral.llm.providers.gemini_oauth import GeminiOAuthProvider
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
async def test_set_api_keys_normalizes_gemini_oauth_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted: list[tuple[str, str]] = []

    async def _fake_upsert(_db, *, key: str, value: str) -> None:
        persisted.append((key, value))

    monkeypatch.setattr(
        "app.services.settings.settings_service.upsert_system_setting",
        _fake_upsert,
    )

    async def _fake_delete(_db, *, key: str) -> None:
        return None

    monkeypatch.setattr(
        "app.services.settings.settings_service.delete_system_setting",
        _fake_delete,
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
        assert gemini.auth_source == "manual"
        assert gemini.masked_key == "refr...oken"
    finally:
        settings.gemini_oauth_credentials = old_oauth
        settings.gemini_api_key = old_api_key


def test_get_api_keys_status_keeps_unavailable_cli_source_selected() -> None:
    instance_settings = settings.model_copy(
        update={
            "openai_api_key": None,
            "openai_oauth_token": None,
            "openai_oauth_source": "cli",
        }
    )

    status = (
        SettingsService().get_api_keys_status(instance_settings).providers[ProviderChoice.OPENAI]
    )

    assert status.configured is False
    assert status.auth_method == "oauth"
    assert status.auth_source == "cli"


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


def test_build_enabled_providers_wraps_cli_oauth_for_live_reload() -> None:
    instance_settings = settings.model_copy(
        update={
            "anthropic_api_key": None,
            "anthropic_oauth_token": None,
            "openai_api_key": None,
            "openai_oauth_token": "codex-token",
            "openai_oauth_source": "cli",
            "gemini_api_key": None,
            "gemini_oauth_credentials": None,
        }
    )

    providers, uses_codex = _build_enabled_providers(instance_settings)

    assert uses_codex is True
    assert isinstance(providers[ProviderChoice.OPENAI], LiveCredentialProvider)


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
        SettingsService._extract_codex_access_token(
            json.dumps({"tokens": {"id_token": "id-token"}})
        )

    assert exc.value.status_code == 422
    assert "access_token" in str(exc.value.detail)


def test_desktop_codex_oauth_status_finds_auth_file(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"tokens":{"access_token":"codex-access-token"}}', encoding="utf-8")

    status = SettingsService().get_desktop_codex_oauth_status(auth_path=auth_path)

    assert status.enabled is True
    assert status.auth_file_found is True


@pytest.mark.asyncio
async def test_codex_reader_picks_up_auth_file_changes(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"tokens":{"access_token":"first"}}', encoding="utf-8")
    assert await read_codex_access_token(auth_path) == "first"

    auth_path.write_text('{"tokens":{"access_token":"second-token"}}', encoding="utf-8")
    assert await read_codex_access_token(auth_path) == "second-token"


@pytest.mark.asyncio
async def test_connect_desktop_codex_oauth_persists_live_source_only(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"tokens":{"access_token":"codex-access-token"}}', encoding="utf-8")
    fake_db = FakeDB()

    result = await SettingsService().connect_desktop_codex_oauth(fake_db, auth_path=auth_path)

    assert result.masked_key == "code...oken"
    rows = {row.key: row.value for row in fake_db.storage[SystemSetting]}
    assert rows["openai_oauth_source"] == "cli"
    assert "openai_oauth_token" not in rows


@pytest.mark.asyncio
async def test_connect_claude_oauth_persists_live_source_only(monkeypatch):
    from sentral.llm import claude_credentials

    async def read():
        return "sk-ant-oat-example-token"

    monkeypatch.setattr(claude_credentials, "read_claude_access_token", read)
    db = FakeDB()
    result = await SettingsService().connect_desktop_claude_oauth(db)
    assert result.masked_key == "sk-a...oken"
    rows = {row.key: row.value for row in db.storage[SystemSetting]}
    assert rows["anthropic_oauth_source"] == "cli"
    assert "anthropic_oauth_token" not in rows


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, OSError("credential source unavailable")])
async def test_import_claude_oauth_failure_leaves_settings_untouched(monkeypatch, failure):
    from sentral.llm import claude_credentials

    async def read():
        if failure:
            raise failure
        return None

    monkeypatch.setattr(claude_credentials, "read_claude_access_token", read)
    db = FakeDB()
    with pytest.raises(HTTPException) as caught:
        await SettingsService().connect_desktop_claude_oauth(db)
    assert caught.value.status_code == (422 if failure else 404)
    assert not db.storage.get(SystemSetting)


@pytest.mark.asyncio
async def test_antigravity_connection_persists_live_source_only(monkeypatch):
    from sentral.llm.providers.gemini_oauth import GeminiOAuthCredentials

    credentials = GeminiOAuthCredentials.parse_input({"refresh_token": "refresh-token"})

    async def read():
        return credentials

    monkeypatch.setattr("sentral.llm.antigravity_credentials.read_antigravity_credentials", read)
    db = FakeDB()
    result = await SettingsService().connect_desktop_gemini_oauth(db)
    assert result.masked_key == "refr...oken"
    rows = {row.key: row.value for row in db.storage[SystemSetting]}
    assert rows["gemini_oauth_source"] == "cli"
    assert "gemini_oauth_credentials" not in rows


@pytest.mark.asyncio
async def test_build_instance_settings_reads_latest_cli_credential(monkeypatch) -> None:
    tokens = iter(["first-token", "second-token"])

    async def read():
        return next(tokens)

    monkeypatch.setattr("sentral.llm.claude_credentials.read_claude_access_token", read)
    db = _FakeSettingsDb([SystemSetting(key="anthropic_oauth_source", value="cli")])
    service = SettingsService()

    first = await service.build_instance_settings(db)
    second = await service.build_instance_settings(db)

    assert first.anthropic_oauth_token == "first-token"
    assert second.anthropic_oauth_token == "second-token"
    assert first.anthropic_oauth_source == second.anthropic_oauth_source == "cli"
