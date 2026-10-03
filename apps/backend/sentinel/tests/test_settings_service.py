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
async def test_provider_routing_persists_per_instance_and_keeps_enabled_cards_first():
    service, db, other = SettingsService(), FakeDB(), FakeDB()
    for provider in ("anthropic", "openai", "gemini"):
        db.add(SystemSetting(key=f"{provider}_api_key", value="test-key"))
    before = await service.build_instance_settings(db)
    assert service.get_provider_routing(before) == {
        "order": ["anthropic", "openai", "gemini", "ollama"],
        "automatic": ["anthropic", "openai", "gemini"],
    }
    await service.set_provider_routing(
        db, order=list(ProviderChoice), automatic=[ProviderChoice.GEMINI, ProviderChoice.OPENAI]
    )
    saved = await service.build_instance_settings(db)
    assert service.get_provider_routing(saved) == {
        "order": ["openai", "gemini", "ollama", "anthropic"],
        "automatic": ["openai", "gemini"],
    }
    assert saved.primary_provider == "openai"
    assert (await service.build_instance_settings(other)).provider_order is None
    await service.set_primary_provider(db, provider=ProviderChoice.ANTHROPIC)
    assert service.get_provider_routing(await service.build_instance_settings(db))["automatic"] == [
        "anthropic",
        "openai",
        "gemini",
    ]
    with pytest.raises(HTTPException, match="Configure providers"):
        await service.set_provider_routing(
            db, order=list(ProviderChoice), automatic=[ProviderChoice.OLLAMA]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider", [ProviderChoice.ANTHROPIC, ProviderChoice.OPENAI, ProviderChoice.GEMINI]
)
async def test_tier_overrides_are_instance_scoped_and_reset_to_defaults(provider):
    service, db, other_db = SettingsService(), FakeDB(), FakeDB()
    before = await service.get_provider_models(db, provider)
    selected = {"fast": "custom-fast", "normal": "custom-normal", "hard": None}
    await service.set_provider_models(db, provider, namespace=provider.value, models=selected)
    saved = await service.get_provider_models(db, provider)
    assert saved["overrides"] == selected
    assert saved["effective"] == {
        **before["defaults"],
        "fast": "custom-fast",
        "normal": "custom-normal",
    }
    assert await service.get_provider_models(other_db, provider) == before
    await service.set_provider_models(
        db, provider, namespace=provider.value, models={tier: None for tier in selected}
    )
    assert await service.get_provider_models(db, provider) == before


@pytest.mark.asyncio
async def test_openai_api_and_codex_overrides_do_not_overwrite_each_other():
    service, db = SettingsService(), FakeDB()
    await service.set_provider_models(
        db,
        ProviderChoice.OPENAI,
        namespace="openai",
        models={"fast": "api-model", "normal": None, "hard": None},
    )
    token = SystemSetting(key="openai_oauth_token", value="test-token")
    db.add(token)
    with pytest.raises(HTTPException) as error:
        await service.set_provider_models(
            db,
            ProviderChoice.OPENAI,
            namespace="openai",
            models={"fast": "wrong-model", "normal": None, "hard": None},
        )
    assert error.value.status_code == 409
    await service.set_provider_models(
        db,
        ProviderChoice.OPENAI,
        namespace="codex",
        models={"fast": "oauth-model", "normal": None, "hard": None},
    )
    assert (await service.get_provider_models(db, ProviderChoice.OPENAI))["effective"][
        "fast"
    ] == "oauth-model"
    await db.delete(token)
    assert (await service.get_provider_models(db, ProviderChoice.OPENAI))["effective"][
        "fast"
    ] == "api-model"


@pytest.mark.asyncio
async def test_ollama_overrides_validate_tools_before_any_write_and_reset_on_server_change(
    monkeypatch,
):
    from unittest.mock import AsyncMock
    from sentral.llm.providers.ollama import OllamaProvider

    service, db = SettingsService(), FakeDB()
    await service.set_ollama(
        db, base_url="https://models.example", model="default:4b", api_key=None
    )
    inspect = AsyncMock(return_value={"capabilities": ["tools"]})
    monkeypatch.setattr(OllamaProvider, "model_info", inspect)
    selected = {"fast": "custom:4b", "normal": "custom:4b", "hard": None}
    await service.set_provider_models(
        db, ProviderChoice.OLLAMA, namespace="ollama", models=selected
    )
    inspect.assert_awaited_once_with("custom:4b")
    saved = await service.get_provider_models(db, ProviderChoice.OLLAMA)
    assert saved["effective"] == {"fast": "custom:4b", "normal": "custom:4b", "hard": "default:4b"}
    inspect.return_value = {"capabilities": []}
    with pytest.raises(HTTPException, match="support tools"):
        await service.set_provider_models(
            db,
            ProviderChoice.OLLAMA,
            namespace="ollama",
            models={**selected, "hard": "unsupported:4b"},
        )
    assert await service.get_provider_models(db, ProviderChoice.OLLAMA) == saved
    await service.set_ollama(
        db, base_url="https://other-models.example", model="other:4b", api_key=None
    )
    after = await service.get_provider_models(db, ProviderChoice.OLLAMA)
    assert after["overrides"] == {tier: None for tier in selected}
    assert set(after["effective"].values()) == {"other:4b"}


@pytest.mark.asyncio
@pytest.mark.parametrize("namespace", ["openai", "codex", "anthropic", "gemini"])
async def test_model_catalog_requests_use_connection_specific_auth_and_pagination(
    namespace, monkeypatch
):
    import httpx
    from sentral.llm.providers.anthropic import AnthropicProvider
    from sentral.llm.providers.codex import CodexProvider
    from sentral.llm.providers.gemini import GeminiProvider
    from sentral.llm.providers.openai import OpenAIProvider

    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "GET"
        if namespace == "codex":
            assert request.url.path == "/backend-api/codex/models"
            assert "client_version" in request.url.params
            assert request.headers["Authorization"] == "Bearer test-token"
            return httpx.Response(200, json={"models": [{"slug": "gpt-6-luna"}]})
        if namespace == "openai":
            assert request.url.path == "/v1/models"
            assert request.headers["Authorization"] == "Bearer test-token"
            return httpx.Response(200, json={"data": [{"id": "gpt-6-luna"}]})
        if namespace == "anthropic":
            assert request.headers["x-api-key"] == "test-token"
            if "after_id" not in request.url.params:
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": "claude-sonnet-5-5"}],
                        "has_more": True,
                        "last_id": "cursor",
                    },
                )
            assert request.url.params["after_id"] == "cursor"
            return httpx.Response(
                200, json={"data": [{"id": "claude-opus-5-5"}], "has_more": False}
            )
        assert request.headers["x-goog-api-key"] == "test-token"
        if "pageToken" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-3.8-flash",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                        {
                            "name": "models/embedding",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ],
                    "nextPageToken": "next",
                },
            )
        assert request.url.params["pageToken"] == "next"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-3.5-flash-lite",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ]
            },
        )

    adapters = {
        "openai": OpenAIProvider,
        "codex": CodexProvider,
        "anthropic": AnthropicProvider,
        "gemini": GeminiProvider,
    }
    adapter = adapters[namespace](
        "test-token",
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    models = await adapter.list_model_ids()
    assert len(models) == (2 if namespace in ("anthropic", "gemini") else 1)
    assert len(requests) == len(models)
    assert "embedding" not in models


@pytest.mark.asyncio
async def test_model_suggestions_failure_keeps_saved_configuration_usable(monkeypatch):
    from unittest.mock import AsyncMock
    import httpx
    from sentral.llm.providers.openai import OpenAIProvider

    service, db = SettingsService(), FakeDB()
    db.add(SystemSetting(key="openai_api_key", value="test-key"))
    await service.set_provider_models(
        db,
        ProviderChoice.OPENAI,
        namespace="openai",
        models={"fast": "gpt-6-luna", "normal": None, "hard": None},
    )
    before = await service.get_provider_models(db, ProviderChoice.OPENAI)
    monkeypatch.setattr(
        OpenAIProvider,
        "list_model_ids",
        AsyncMock(side_effect=httpx.ConnectError("private upstream body")),
    )
    result = await service.provider_model_options(db, ProviderChoice.OPENAI)
    assert result["models"] == []
    assert "private upstream body" not in result["message"]
    assert await service.get_provider_models(db, ProviderChoice.OPENAI) == before


@pytest.mark.asyncio
async def test_gemini_oauth_suggestions_use_reported_quota_ids_not_the_api_key_catalog(monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.settings.provider_usage import (
        ProviderUsage,
        normalize_usage,
        provider_usage_service,
    )
    from sentral.llm.providers.gemini import GeminiProvider

    service, db = SettingsService(), FakeDB()
    db.add(SystemSetting(key="gemini_oauth_credentials", value="test-oauth-bundle"))
    usage = ProviderUsage(
        status="available",
        windows=normalize_usage(
            "gemini",
            {
                "buckets": [
                    {"modelId": "gemini-3.8-flash-tiered", "remainingFraction": 1},
                    {"modelId": "claude-sonnet-4-6", "remainingFraction": 1},
                ]
            },
        ),
    )
    monkeypatch.setattr(provider_usage_service, "get_usage", AsyncMock(return_value=usage))
    catalog = AsyncMock(side_effect=AssertionError("OAuth must not use the API-key catalog"))
    monkeypatch.setattr(GeminiProvider, "list_model_ids", catalog)
    result = await service.provider_model_options(db, ProviderChoice.GEMINI)
    assert result["models"] == ["gemini-3.8-flash-tiered"]
    assert "quotas" in result["message"]
    catalog.assert_not_awaited()


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
