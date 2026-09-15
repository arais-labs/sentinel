from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.routers import settings as settings_router
from app.routers.settings import DeleteProviderRequest, SetApiKeysRequest, SetPrimaryProviderRequest
from sentral.llm.ids import ProviderChoice


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
