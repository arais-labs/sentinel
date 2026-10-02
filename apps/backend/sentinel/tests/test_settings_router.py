from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.routers import settings as settings_router
from app.routers.settings import DeleteProviderRequest, SetApiKeysRequest, SetPrimaryProviderRequest
from sentral.llm.ids import ProviderChoice
from pydantic import ValidationError


@pytest.mark.parametrize(
    "order,automatic",
    [
        (["anthropic", "openai", "gemini"], ["anthropic"]),
        (["anthropic", "anthropic", "gemini", "ollama"], ["anthropic"]),
        (["anthropic", "openai", "gemini", "ollama"], []),
        (["anthropic", "openai", "gemini", "ollama"], ["openai", "openai"]),
        (["anthropic", "openai", "gemini", "ollama"], ["unknown"]),
    ],
)
def test_routing_rejects_missing_duplicate_unknown_or_empty_choices(order, automatic):
    with pytest.raises(ValidationError):
        settings_router.SetProviderRoutingRequest(order=order, automatic=automatic)


@pytest.mark.asyncio
async def test_routing_save_rebuilds_runtime_and_returns_authoritative_order(monkeypatch):
    from unittest.mock import AsyncMock
    from tests.fake_db import FakeDB
    from app.models.system import SystemSetting
    from app.services.settings.settings_service import SettingsService

    rebuild = AsyncMock()
    monkeypatch.setattr(settings_router, "_rebuild_current_instance_runtime_context", rebuild)
    db = FakeDB()
    db.add(SystemSetting(key="openai_api_key", value="test-key"))
    request = SimpleNamespace()
    result = await settings_router.set_provider_routing(
        settings_router.SetProviderRoutingRequest(
            order=list(ProviderChoice), automatic=[ProviderChoice.OPENAI]
        ),
        request,
        db,
        SettingsService(),
    )
    assert result == {"order": ["openai", "ollama", "anthropic", "gemini"], "automatic": ["openai"]}
    rebuild.assert_awaited_once_with(request)


@pytest.mark.parametrize("model", ["a model", "https://example.com/model", "bad\nmodel", "x" * 201])
def test_provider_model_ids_reject_display_names_urls_and_control_characters(model):
    with pytest.raises(ValidationError):
        settings_router.SetProviderModelsRequest(
            namespace="openai", fast=model, normal=None, hard=None
        )


@pytest.mark.asyncio
async def test_model_save_rebuilds_current_runtime_and_returns_effective_settings(monkeypatch):
    from unittest.mock import AsyncMock
    from tests.fake_db import FakeDB
    from app.services.settings.settings_service import SettingsService

    rebuild = AsyncMock()
    monkeypatch.setattr(settings_router, "_rebuild_current_instance_runtime_context", rebuild)
    request = SimpleNamespace()
    result = await settings_router.set_provider_models(
        ProviderChoice.ANTHROPIC,
        settings_router.SetProviderModelsRequest(
            namespace="anthropic", fast="claude-haiku-4-5", normal="  ", hard=None
        ),
        request,
        FakeDB(),
        SettingsService(),
    )
    assert result["effective"]["fast"] == "claude-haiku-4-5"
    assert result["effective"]["normal"] == result["defaults"]["normal"]
    assert result["overrides"]["normal"] is None
    rebuild.assert_awaited_once_with(request)


class _SettingsService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def set_api_keys(self, _db, **_kwargs) -> None:
        self.calls.append("set_api_keys")

    async def delete_api_keys(self, _db, *, provider: ProviderChoice) -> None:
        self.calls.append(f"delete_api_keys:{provider.value}")

    async def set_primary_provider(self, _db, *, provider: ProviderChoice) -> None:
        self.calls.append(f"set_primary_provider:{provider.value}")


@pytest.mark.asyncio
async def test_settings_mutations_rebuild_current_instance_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rebuilt: list[str] = []

    async def rebuild(_request) -> None:
        rebuilt.append("rebuild")

    monkeypatch.setattr(settings_router, "_rebuild_current_instance_runtime_context", rebuild)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    service = _SettingsService()

    response = await settings_router.set_api_keys(
        SetApiKeysRequest(openai_api_key="sk-test"),
        request,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        service,  # type: ignore[arg-type]
    )
    assert response == {"success": True}

    response = await settings_router.delete_api_keys(
        DeleteProviderRequest(provider=ProviderChoice.OPENAI),
        request,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        service,  # type: ignore[arg-type]
    )
    assert response == {"success": True}

    response = await settings_router.set_primary_provider(
        SetPrimaryProviderRequest(provider=ProviderChoice.GEMINI),
        request,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        service,  # type: ignore[arg-type]
    )
    assert response == {"success": True, "primary_provider": "gemini"}

    assert service.calls == [
        "set_api_keys",
        "delete_api_keys:openai",
        "set_primary_provider:gemini",
    ]
    assert rebuilt == ["rebuild", "rebuild", "rebuild"]


@pytest.mark.asyncio
async def test_claude_connection_rebuilds_runtime_and_returns_mask_only(monkeypatch):
    calls = []

    class Service:
        async def connect_desktop_claude_oauth(self, db):
            calls.append("import")
            return SimpleNamespace(masked_key="sk-a...test")

    async def rebuild(request):
        calls.append("rebuild")

    monkeypatch.setattr(settings_router, "_rebuild_current_instance_runtime_context", rebuild)
    result = await settings_router.connect_desktop_claude_oauth(
        SimpleNamespace(), object(), Service()
    )
    assert result == {"success": True, "masked_key": "sk-a...test"}
    assert calls == ["import", "rebuild"]


@pytest.mark.asyncio
async def test_gemini_connection_rebuilds_runtime_and_returns_mask_only(monkeypatch):
    calls = []

    class Service:
        async def connect_desktop_gemini_oauth(self, db):
            calls.append("import")
            return SimpleNamespace(masked_key="refr...oken")

    async def rebuild(request):
        calls.append("rebuild")

    monkeypatch.setattr(settings_router, "_rebuild_current_instance_runtime_context", rebuild)
    result = await settings_router.connect_desktop_gemini_oauth(
        SimpleNamespace(), object(), Service()
    )
    assert result == {"success": True, "masked_key": "refr...oken"}
    assert calls == ["import", "rebuild"]
