import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.services.agent import PreparedRuntimeTurnContext, ToolAdapter
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import AssistantMessage, TextContent, TokenUsage
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRegistry
from tests.fake_db import FakeDB


class _FakeProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = []

    @property
    def name(self) -> str:
        return "fake"

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "tools": list(tools or []),
                "temperature": temperature,
            }
        )
        return AssistantMessage(
            content=[TextContent(text="Agent response")],
            model="fake-model",
            provider="fake",
            usage=TokenUsage(input_tokens=11, output_tokens=7),
            stop_reason="stop",
        )

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        _ = (messages, model, tools, temperature, reasoning_config, tool_choice)
        if False:
            yield


class _FakeLoop:
    def __init__(self) -> None:
        self.calls = []
        self.provider = _FakeProvider()
        self.tool_adapter = ToolAdapter(ToolRegistry(), ToolExecutor(ToolRegistry()))
        self._estop = SimpleNamespace(check_level=self._check_level)

    async def _check_level(self, _db):
        return None

    async def estop_level(self, db):
        return await self._estop.check_level(db)

    async def prepare_runtime_turn_context(
        self,
        db,
        session_id,
        *,
        system_prompt,
        pending_user_message,
        agent_mode,
        model,
        temperature,
        max_iterations,
        stream,
    ):
        self.calls.append(
            {
                "session_id": session_id,
                "user_message": pending_user_message,
                "agent_mode": agent_mode,
                "model": model,
                "temperature": temperature,
                "max_iterations": max_iterations,
                "stream": stream,
                "system_prompt": system_prompt,
            }
        )
        return PreparedRuntimeTurnContext(
            messages=[],
            tools=[],
            effective_system_prompt=system_prompt,
            runtime_context_snapshot=None,
        )

    async def persist_created_messages(self, db, session_id, created, assistant_iterations, **kwargs):
        _ = (db, session_id, created, assistant_iterations, kwargs)

    def extract_final_text(self, _messages) -> str:
        return "Agent response"

    def collect_attachments(self, _messages) -> list[dict]:
        return []


def test_chat_endpoint_calls_runtime_support_and_returns_response():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    fake_loop = _FakeLoop()
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        old_agent_runtime_support = getattr(app.state, "agent_runtime_support", None)
        app.state.agent_runtime_support = fake_loop
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "chat"}, headers=headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        with patch(
            "app.services.sessions.service.SessionNamingService.maybe_auto_rename",
            new=AsyncMock(return_value=None),
        ):
            chat = client.post(
                f"/api/v1/sessions/{session_id}/chat",
                json={"content": "hello model", "tier": "normal"},
                headers=headers,
            )
        assert chat.status_code == 200
        payload = chat.json()
        assert payload["response"] == "Agent response"
        assert payload["iterations"] == 1
        assert payload["usage"] == {"input_tokens": 11, "output_tokens": 7}
        assert fake_loop.calls[0]["user_message"] == "hello model"
        assert fake_loop.calls[0]["agent_mode"] == "normal"
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
        if "old_agent_runtime_support" in locals():
            app.state.agent_runtime_support = old_agent_runtime_support


def test_chat_endpoint_returns_503_when_no_provider_configured():
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
        old_agent_runtime_support = getattr(app.state, "agent_runtime_support", None)
        app.state.agent_runtime_support = None
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "chat"}, headers=headers)
        session_id = session_resp.json()["id"]

        chat = client.post(
            f"/api/v1/sessions/{session_id}/chat",
            json={"content": "hello"},
            headers=headers,
        )
        assert chat.status_code == 503
        assert chat.json()["error"]["code"] == "internal_error"
        assert chat.json()["error"]["message"] == "No LLM provider configured"
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
        if "old_agent_runtime_support" in locals():
            app.state.agent_runtime_support = old_agent_runtime_support
