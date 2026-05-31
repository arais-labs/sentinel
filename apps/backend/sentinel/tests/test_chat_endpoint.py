import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.routers import sessions as sessions_router
from app.main import app
from app.services.agent import PreparedRuntimeTurnContext
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import AssistantMessage, TextContent, TokenUsage
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRegistry
from tests.fake_db import FakeDB
from tests.helpers import install_fake_db_overrides, restore_test_app


class _FakeProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = []

    @property
    def name(self) -> str:
        return "fake"

    async def chat(
        self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None
    ):
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

    async def stream(
        self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None
    ):
        _ = (messages, model, tools, temperature, reasoning_config, tool_choice)
        if False:
            yield


class _FakeLoop:
    def __init__(self) -> None:
        self.calls = []
        self.provider = _FakeProvider()
        registry = ToolRegistry()
        self.tool_registry = registry
        self.tool_executor = ToolExecutor(registry)

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

    async def persist_created_messages(
        self, db, session_id, created, assistant_iterations, **kwargs
    ):
        _ = (db, session_id, created, assistant_iterations, kwargs)

    def extract_final_text(self, _messages) -> str:
        return "Agent response"

    def collect_attachments(self, _messages) -> list[dict]:
        return []


def test_chat_endpoint_calls_runtime_support_and_returns_response():
    fake_db = FakeDB()

    fake_loop = _FakeLoop()
    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        old_runtime_context = sessions_router.get_request_instance_runtime_context
        sessions_router.get_request_instance_runtime_context = lambda _request: SimpleNamespace(
            agent_runtime_support=fake_loop
        )
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_resp = client.post(
            "/api/v1/instances/main/sessions", json={"title": "chat"}, headers=headers
        )
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        with patch(
            "app.services.sessions.service.SessionNamingService.maybe_auto_rename",
            new=AsyncMock(return_value=None),
        ):
            chat = client.post(
                f"/api/v1/instances/main/sessions/{session_id}/chat",
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
        restore_test_app(old_init)
        if "old_runtime_context" in locals():
            sessions_router.get_request_instance_runtime_context = old_runtime_context


def test_chat_endpoint_returns_503_when_no_provider_configured():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        old_runtime_context = sessions_router.get_request_instance_runtime_context
        sessions_router.get_request_instance_runtime_context = lambda _request: SimpleNamespace(
            agent_runtime_support=None
        )
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_resp = client.post(
            "/api/v1/instances/main/sessions", json={"title": "chat"}, headers=headers
        )
        session_id = session_resp.json()["id"]

        chat = client.post(
            f"/api/v1/instances/main/sessions/{session_id}/chat",
            json={"content": "hello"},
            headers=headers,
        )
        assert chat.status_code == 503
        assert chat.json()["error"]["code"] == "internal_error"
        assert chat.json()["error"]["message"] == "No LLM provider configured"
    finally:
        restore_test_app(old_init)
        if "old_runtime_context" in locals():
            sessions_router.get_request_instance_runtime_context = old_runtime_context
