from __future__ import annotations

import asyncio
import os

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import Session, SystemSetting
from app.services.agent import AgentLoop, ContextBuilder, ToolAdapter
from app.services.estop import EstopLevel, EstopService
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import AgentEvent, AssistantMessage, TextContent, ToolCallContent
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolDefinition, ToolRegistry
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


class _SequenceProvider(LLMProvider):
    def __init__(self, response: AssistantMessage) -> None:
        self._response = response
        self.calls = 0

    @property
    def name(self) -> str:
        return "sequence"

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None):
        _ = messages, model, tools, temperature
        self.calls += 1
        return self._response

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None):
        _ = messages, model, tools, temperature
        if False:
            yield AgentEvent(type="done", stop_reason="stop")
        return


def test_estop_level_enum_values():
    assert int(EstopLevel.NONE) == 0
    assert int(EstopLevel.TOOL_FREEZE) == 1
    assert int(EstopLevel.NETWORK_KILL) == 2
    assert int(EstopLevel.KILL_ALL) == 3


def test_estop_service_default_and_legacy_compatibility():
    db = FakeDB()
    service = EstopService()
    assert _run(service.check_level(db)) == EstopLevel.NONE

    db.add(SystemSetting(key=service.LEGACY_ACTIVE_KEY, value="true"))
    assert _run(service.check_level(db)) == EstopLevel.TOOL_FREEZE


def test_estop_service_enforce_tool_rules():
    db = FakeDB()
    service = EstopService()

    _run(service.set_level(db, EstopLevel.NONE))
    _run(service.enforce_tool(db, "file_read", "low"))

    _run(service.set_level(db, EstopLevel.TOOL_FREEZE))
    try:
        _run(service.enforce_tool(db, "file_read", "low"))
        raised = False
    except PermissionError:
        raised = True
    assert raised is True

    _run(service.set_level(db, EstopLevel.NETWORK_KILL))
    try:
        _run(service.enforce_tool(db, "browser_navigate", "medium"))
        raised_network = False
    except PermissionError:
        raised_network = True
    assert raised_network is True

    _run(service.set_level(db, EstopLevel.KILL_ALL))
    try:
        _run(service.enforce_tool(db, "file_read", "low"))
        raised_kill_all = False
    except PermissionError:
        raised_kill_all = True
    assert raised_kill_all is True


def test_admin_estop_level_endpoints_persist_state():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        activated = client.post("/api/v1/admin/estop?level=2", headers=headers)
        assert activated.status_code == 200
        assert activated.json()["level"] == 2

        current = client.get("/api/v1/admin/estop", headers=headers)
        assert current.status_code == 200
        assert current.json()["level"] == 2
        assert current.json()["active"] is True

        cleared = client.delete("/api/v1/admin/estop", headers=headers)
        assert cleared.status_code == 200
        assert cleared.json()["level"] == 0

        config = client.get("/api/v1/admin/config", headers=headers)
        assert config.status_code == 200
        assert config.json()["estop_active"] is False
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_tool_adapter_checks_estop_before_execution():
    db = FakeDB()
    service = EstopService()
    _run(service.set_level(db, EstopLevel.TOOL_FREEZE))

    registry = ToolRegistry()

    async def _echo(payload):
        return payload

    registry.register(
        ToolDefinition(
            name="echo",
            description="echo",
            risk_level="low",
            parameters_schema={"type": "object", "additionalProperties": True},
            execute=_echo,
        )
    )

    adapter = ToolAdapter(registry, ToolExecutor(registry))
    results = _run(
        adapter.execute_tool_calls(
            [ToolCallContent(id="c1", name="echo", arguments={})],
            db,
            allow_high_risk=True,
        )
    )
    assert len(results) == 1
    assert results[0].is_error is True
    assert "Emergency stop" in results[0].content


def test_tool_adapter_enforces_estop_per_call_not_once_per_batch():
    class _PerCallEstop(EstopService):
        def __init__(self) -> None:
            self.calls = 0

        async def enforce_tool(self, db, tool_name: str, risk_level: str) -> None:  # noqa: ARG002
            self.calls += 1
            if self.calls >= 2:
                raise PermissionError("Emergency stop TOOL_FREEZE blocks all tool execution")

    db = FakeDB()
    registry = ToolRegistry()

    async def _echo(payload):
        return payload

    registry.register(
        ToolDefinition(
            name="echo",
            description="echo",
            risk_level="low",
            parameters_schema={"type": "object", "additionalProperties": True},
            execute=_echo,
        )
    )

    estop = _PerCallEstop()
    adapter = ToolAdapter(registry, ToolExecutor(registry), estop_service=estop)
    results = _run(
        adapter.execute_tool_calls(
            [
                ToolCallContent(id="c1", name="echo", arguments={"v": 1}),
                ToolCallContent(id="c2", name="echo", arguments={"v": 2}),
            ],
            db,
            allow_high_risk=True,
        )
    )

    assert len(results) == 2
    assert sum(1 for item in results if item.is_error) == 1
    assert sum(1 for item in results if not item.is_error) == 1
    assert estop.calls == 2


def test_agent_loop_aborts_when_kill_all_active():
    db = FakeDB()
    service = EstopService()
    _run(service.set_level(db, EstopLevel.KILL_ALL))

    session = Session(user_id="dev-admin", status="active", title="estop")
    db.add(session)

    provider = _SequenceProvider(
        AssistantMessage(
            content=[TextContent(text="should not run")],
            model="m",
            provider="p",
            stop_reason="stop",
        )
    )
    loop = AgentLoop(
        provider,
        ContextBuilder(default_system_prompt="system"),
        ToolAdapter(ToolRegistry(), ToolExecutor(ToolRegistry())),
    )
    events: list[AgentEvent] = []

    async def _capture(event: AgentEvent) -> None:
        events.append(event)

    result = _run(loop.run(db, session.id, "hello", stream=False, on_event=_capture))
    assert result.iterations == 0
    assert provider.calls == 0
    assert any(event.type == "done" and event.stop_reason == "aborted" for event in events)
