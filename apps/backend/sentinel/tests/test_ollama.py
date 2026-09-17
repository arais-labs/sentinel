from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from sentral import (
    AgentRuntimeEngine,
    ConversationItem,
    GenerationConfig,
    RunTurnRequest,
    TextBlock,
)
from sentral.llm.runtime_adapter import SentinelProviderAdapter
from sentral.llm.generic.types import UserMessage, ToolResultMessage, ToolCallContent
from sentral.llm.providers.ollama import OllamaProvider, normalize_endpoint
from app.routers.ollama import EndpointRequest, endpoint_provider
from app.services.llm.ollama_models import OllamaPulls, validate_model
from app.routers import ollama as routes
from app.services.llm.factory import build_tier_provider_from_settings
from sentral import ToolDefinition, ToolExecutionResult
from app.services.settings.settings_service import SettingsService
from tests.fake_db import FakeDB


def provider(handler, url="https://remote.example/ollama"):
    return OllamaProvider(
        url,
        "test-secret",
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/model",
        "http://user:pw@host",
        "https://host?key=secret",
        "https://host/#fragment",
        "http://host:invalid",
    ],
)
def test_rejects_unsafe_endpoint_formats(url):
    with pytest.raises(ValueError):
        normalize_endpoint(url)


@pytest.mark.asyncio
async def test_remote_discovery_chat_and_tool_replay():
    seen = []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert str(request.url).startswith("https://remote.example/ollama/api/")
        seen.append(request)
        if request.url.path.endswith("tags"):
            return httpx.Response(200, json={"models": [{"name": "test:4b"}]})
        if request.url.path.endswith("show"):
            return httpx.Response(200, json={"capabilities": ["tools"]})
        return httpx.Response(
            200,
            json={
                "message": {
                    "tool_calls": [
                        {"function": {"name": "sessions", "arguments": {"action": "list_sessions"}}}
                    ]
                },
                "done": True,
                "prompt_eval_count": 12,
                "eval_count": 3,
            },
        )

    llm = provider(handler)
    assert (await llm.discover_models())[0]["name"] == "test:4b"
    assert (await llm.model_info("test:4b"))["capabilities"] == ["tools"]
    reply = await llm.chat([UserMessage(content="list chats")], "test:4b")
    call = reply.content[0]
    assert isinstance(call, ToolCallContent) and call.id
    assert reply.stop_reason == "tool_use" and reply.usage.input_tokens == 12
    await llm.chat(
        [reply, ToolResultMessage(tool_call_id=call.id, tool_name="sessions", content="[]")],
        "test:4b",
        tool_choice="none",
    )
    payload = json.loads(seen[-1].content)
    assert payload["messages"][-1]["tool_name"] == "sessions"
    assert isinstance(payload["messages"][0]["tool_calls"][0]["function"]["arguments"], dict)
    assert "tools" not in payload
    assert payload["think"] is False


@pytest.mark.asyncio
async def test_native_stream_runs_through_real_sentral_engine():
    step = 0

    def handler(request):
        nonlocal step
        step += 1
        data = json.loads(request.content)
        assert data["stream"] is True
        if step == 1:
            chunks = [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "chats_list",
                                    "arguments": {},
                                }
                            }
                        ]
                    },
                    "done": False,
                },
                {"message": {}, "done": True},
            ]
        else:
            assert data["messages"][-1]["tool_name"] == "chats_list"
            chunks = [
                {"message": {"content": "No chats "}, "done": False},
                {"message": {"content": "are running."}, "done": True},
            ]
        return httpx.Response(200, text="\n".join(json.dumps(chunk) for chunk in chunks))

    execute = AsyncMock(return_value={"sessions": []})

    class Tools:
        def __init__(self):
            async def run(args):
                return ToolExecutionResult(status="ok", content=await execute(args))

            self.tools = [
                ToolDefinition(
                    name="chats_list",
                    description="List chats",
                    parameters_schema={"type": "object", "properties": {}},
                    execute=run,
                )
            ]

        def list_tools(self):
            return self.tools

        def get_tool(self, name):
            return next((tool for tool in self.tools if tool.name == name), None)

    engine = AgentRuntimeEngine(
        provider=SentinelProviderAdapter(provider(handler)), tool_registry=Tools()
    )
    result = await engine.run_turn(
        RunTurnRequest(
            history=[],
            new_items=[
                ConversationItem(id="user", role="user", content=[TextBlock(text="list chats")])
            ],
            config=GenerationConfig(model="test:4b", stream=True, max_iterations=3),
        )
    )
    assert result.status == "completed"
    assert result.final_item.content[0].text == "No chats are running."
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_truncated_stream_is_not_success():
    llm = provider(
        lambda _: httpx.Response(200, text='{"message":{"content":"partial"},"done":false}\n')
    )
    with pytest.raises(ValueError, match="before completion"):
        _ = [event async for event in llm.stream([UserMessage(content="hello")], "model")]


@pytest.mark.asyncio
async def test_saved_token_is_never_reused_for_a_different_endpoint():
    service = SimpleNamespace(
        build_instance_settings=AsyncMock(
            return_value=SimpleNamespace(
                ollama_base_url="https://original.example", ollama_api_key="original-secret"
            )
        )
    )
    _, key = await endpoint_provider(
        EndpointRequest(base_url="https://different.example"), None, service
    )
    assert key is None
    _, key = await endpoint_provider(
        EndpointRequest(base_url="https://original.example"), None, service
    )
    assert key == "original-secret"
    _, key = await endpoint_provider(
        EndpointRequest(base_url="https://original.example", api_key=""), None, service
    )
    assert key == ""


@pytest.mark.asyncio
async def test_ollama_configuration_persists_and_routes_in_all_tiers():
    db = FakeDB()
    service = SettingsService()
    await service.set_ollama(db, base_url="http://127.0.0.1:11434", model="test:4b", api_key=None)
    config = await service.build_instance_settings(db)
    config.primary_provider = "ollama"
    router = build_tier_provider_from_settings(config)
    for tier in ("fast", "normal", "hard"):
        assert router.resolve_generation_hint(tier) == ("ollama", "test:4b")
        assert router.model_context(tier)["context_window_tokens"] == 32768
    assert service.get_api_keys_status(config).providers["ollama"].configured
    await service.delete_api_keys(db, provider="ollama")
    assert not (await service.build_instance_settings(db)).ollama_model


def test_model_names_are_validated():
    for invalid in ("--help", "model;whoami", "name with spaces", "$(pwd)"):
        with pytest.raises(ValueError):
            validate_model(invalid)


@pytest.mark.asyncio
async def test_pull_is_background_endpoint_scoped_and_reports_layer_progress():
    resume = asyncio.Event()
    progress = asyncio.Event()
    closed = asyncio.Event()

    async def stream(model):
        try:
            yield {"status": "pulling", "digest": "a", "total": 100, "completed": 80}
            yield {"status": "pulling", "digest": "b", "total": 100, "completed": 20}
            progress.set()
            await resume.wait()
            yield {"status": "success"}
        finally:
            closed.set()

    llm = SimpleNamespace(base_url="https://remote.example", pull_model=stream)
    pulls = OllamaPulls()
    result = await pulls.start("instance", llm, "test:4b")
    assert result["phase"] == "running"
    await progress.wait()
    assert pulls.status("instance", llm.base_url)["completed"] == 100
    assert pulls.status("instance", llm.base_url)["total"] == 200
    assert pulls.status("another", llm.base_url)["phase"] == "idle"
    assert pulls.status("instance", "http://localhost:11434")["phase"] == "idle"
    with pytest.raises(ValueError, match="already downloading"):
        await pulls.start("instance", llm, "other")
    resume.set()
    await pulls.tasks[("instance", llm.base_url)]
    assert pulls.status("instance", llm.base_url)["phase"] == "succeeded"
    assert closed.is_set()
    await pulls.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "https://remote.example/ollama"])
async def test_pull_and_delete_use_selected_endpoint_and_auth(url):
    seen = []

    def handler(request):
        seen.append(request)
        assert str(request.url).startswith(url + "/api/")
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert json.loads(request.content)["model"] == "test:4b"
        return httpx.Response(200, text='{"status":"success"}\n')

    llm = provider(handler, url)
    assert [event async for event in llm.pull_model("test:4b")] == [{"status": "success"}]
    await llm.delete_model("test:4b")
    assert [(r.method, r.url.path.split("/")[-1]) for r in seen] == [
        ("POST", "pull"),
        ("DELETE", "delete"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,detail",
    [
        (200, '{"status":"pulling"}\n', "closed early"),
        (200, '{"error":"model does not exist"}\n', "Model not found"),
        (401, "secret internal info", "Access denied"),
        (405, "", "does not allow"),
    ],
)
async def test_download_errors_are_actionable_and_not_success(status, body, detail):
    llm = provider(lambda _: httpx.Response(status, text=body))
    pulls = OllamaPulls()
    await pulls.start("instance", llm, "test:4b")
    await pulls.tasks[("instance", llm.base_url)]
    result = pulls.status("instance", llm.base_url)
    assert result["phase"] == "failed"
    assert detail in result["detail"]
    assert "secret" not in result["detail"]
    await pulls.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("target,cleared", [("selected:4b", True), ("other:4b", False)])
async def test_removing_model_only_clears_matching_selection(monkeypatch, target, cleared):
    db, service = FakeDB(), SettingsService()
    await service.set_ollama(
        db, base_url="https://remote.example", model="selected:4b", api_key="keep-key"
    )
    llm = SimpleNamespace(delete_model=AsyncMock())
    monkeypatch.setattr(routes, "endpoint_provider", AsyncMock(return_value=(llm, "keep-key")))
    monkeypatch.setattr(
        routes,
        "get_request_instance_runtime_context",
        lambda _: SimpleNamespace(database_name="instance"),
    )
    rebuild = AsyncMock()
    monkeypatch.setattr(routes, "_rebuild_current_instance_runtime_context", rebuild)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(ollama_pulls=OllamaPulls()))
    )
    result = await routes.remove_model(
        routes.SaveEndpoint(base_url="https://remote.example", model=target), request, db, service
    )
    assert result["cleared_selection"] is cleared
    llm.delete_model.assert_awaited_once_with(target)
    config = await service.build_instance_settings(db)
    assert config.ollama_model == ("" if cleared else "selected:4b")
    assert config.ollama_base_url == "https://remote.example"
    assert config.ollama_api_key == "keep-key"
    assert rebuild.await_count == int(cleared)


@pytest.mark.asyncio
@pytest.mark.parametrize("running", [True, False])
async def test_blocked_or_failed_deletion_keeps_configuration(monkeypatch, running):
    db, service = FakeDB(), SettingsService()
    await service.set_ollama(
        db, base_url="https://remote.example", model="selected:4b", api_key="keep-key"
    )
    request_error = httpx.Request("DELETE", "https://remote.example/api/delete")
    error = httpx.HTTPStatusError(
        "private server text",
        request=request_error,
        response=httpx.Response(403, request=request_error),
    )
    llm = SimpleNamespace(delete_model=AsyncMock(side_effect=error))
    monkeypatch.setattr(routes, "endpoint_provider", AsyncMock(return_value=(llm, "keep-key")))
    monkeypatch.setattr(
        routes,
        "get_request_instance_runtime_context",
        lambda _: SimpleNamespace(database_name="instance"),
    )
    rebuild = AsyncMock()
    monkeypatch.setattr(routes, "_rebuild_current_instance_runtime_context", rebuild)
    pulls = OllamaPulls()
    if running:
        pulls.jobs[("instance", "https://remote.example")] = {"phase": "running"}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ollama_pulls=pulls)))
    with pytest.raises(routes.HTTPException) as caught:
        await routes.remove_model(
            routes.SaveEndpoint(base_url="https://remote.example", model="selected:4b"),
            request,
            db,
            service,
        )
    assert caught.value.status_code == (409 if running else 502)
    assert "private server text" not in caught.value.detail
    assert llm.delete_model.await_count == (0 if running else 1)
    assert (await service.build_instance_settings(db)).ollama_model == "selected:4b"
    rebuild.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_closes_only_owned_download_stream():
    started, closed = asyncio.Event(), asyncio.Event()

    async def stream(model):
        try:
            started.set()
            await asyncio.Event().wait()
            yield {"status": "success"}
        finally:
            closed.set()

    llm = SimpleNamespace(base_url="https://remote.example", pull_model=stream)
    pulls = OllamaPulls()
    await pulls.start("instance", llm, "test:4b")
    await started.wait()
    await pulls.close()
    assert closed.is_set()
    assert pulls.status("instance", llm.base_url)["phase"] == "failed"
    with pytest.raises(ValueError, match="shutting down"):
        await pulls.start("instance", llm, "test:4b")
