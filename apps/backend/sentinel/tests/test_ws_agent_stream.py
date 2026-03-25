import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.config import settings
from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.services.agent import PreparedRuntimeTurnContext, ToolAdapter
from app.services.sessions.compaction import CompactionResult
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import AgentEvent
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRegistry
from tests.fake_db import FakeDB


class _FakeProvider(LLMProvider):
    def __init__(self, deltas_by_run: list[list[str]] | None = None) -> None:
        self._deltas_by_run = deltas_by_run or [["agent ", "says hi"]]
        self.calls = 0

    @property
    def name(self) -> str:
        return "fake"

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        _ = (messages, model, tools, temperature, reasoning_config, tool_choice)
        raise AssertionError("WS runtime tests expect the streaming provider path")

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        _ = (messages, model, tools, temperature, reasoning_config, tool_choice)
        run_idx = min(self.calls, len(self._deltas_by_run) - 1)
        self.calls += 1
        for delta in self._deltas_by_run[run_idx]:
            yield AgentEvent(type="text_delta", delta=delta)
        yield AgentEvent(type="done", stop_reason="stop")


class _FakeLoop:
    def __init__(self, deltas_by_run: list[list[str]] | None = None) -> None:
        self._estop = SimpleNamespace(check_level=self._check_level)
        self.context_builder = SimpleNamespace(build=self._build_context)
        registry = ToolRegistry()
        self.tool_adapter = ToolAdapter(registry, ToolExecutor(registry))
        self.provider = _FakeProvider(deltas_by_run)

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
        messages = await self.context_builder.build(db, session_id, system_prompt, pending_user_message, agent_mode)
        return PreparedRuntimeTurnContext(
            messages=messages,
            tools=self.tool_adapter.get_tool_schemas(),
            effective_system_prompt=system_prompt,
            runtime_context_snapshot=None,
        )

    async def persist_created_messages(self, db, session_id, created, assistant_iterations, **kwargs):
        await self._persist_messages(db, session_id, created, assistant_iterations, **kwargs)

    async def _check_level(self, _db):
        return None

    async def _build_context(self, _db, _session_id, system_prompt, pending_user_message, agent_mode):
        _ = (system_prompt, pending_user_message, agent_mode)
        return []

    async def _persist_messages(self, db, session_id, created, assistant_iterations, **kwargs):
        _ = (db, session_id, created, assistant_iterations, kwargs)

    def _extract_final_text(self, _messages) -> str:
        deltas = self.provider._deltas_by_run[min(self.provider.calls - 1, len(self.provider._deltas_by_run) - 1)]
        return "".join(deltas)

    @staticmethod
    def _collect_attachments(_messages) -> list[dict]:
        return []

    def extract_final_text(self, messages) -> str:
        return self._extract_final_text(messages)

    def collect_attachments(self, messages) -> list[dict]:
        return self._collect_attachments(messages)


def test_ws_streams_agent_loop_events_when_provider_available():
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
        app.state.agent_runtime_support = _FakeLoop()
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "ws-stream"}, headers=headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        with patch(
            "app.routers.ws.SessionNamingService.maybe_auto_rename",
            new=AsyncMock(return_value=None),
        ):
            with client.websocket_connect(f"/ws/sessions/{session_id}/stream?token={token}") as ws:
                connected = ws.receive_json()
                assert connected["type"] == "connected"

                ws.send_json({"type": "message", "content": "hello"})
                ack = ws.receive_json()
                assert ack["type"] == "message_ack"

                thinking = ws.receive_json()
                assert thinking["type"] == "agent_thinking"

                progress = ws.receive_json()
                assert progress["type"] == "agent_progress"
                assert progress["iteration"] == 1

                text_delta_1 = ws.receive_json()
                assert text_delta_1["type"] == "text_delta"
                assert text_delta_1["delta"] == "agent "

                text_delta_2 = ws.receive_json()
                assert text_delta_2["type"] == "text_delta"
                assert text_delta_2["delta"] == "says hi"

                done = ws.receive_json()
                assert done["type"] == "done"
                assert done["stop_reason"] == "stop"
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
        if "old_agent_runtime_support" in locals():
            app.state.agent_runtime_support = old_agent_runtime_support


def test_ws_auto_resumes_after_compaction():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    old_auto_resume = settings.compaction_auto_resume_enabled
    app_main.init_db = _noop_init_db
    settings.compaction_auto_resume_enabled = True
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        old_agent_runtime_support = getattr(app.state, "agent_runtime_support", None)
        fake_loop = _FakeLoop(deltas_by_run=[["first run"], ["resumed run"]])
        app.state.agent_runtime_support = fake_loop
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "ws-resume"}, headers=headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        with (
            patch(
                "app.routers.ws.CompactionService.should_auto_compact",
                new=AsyncMock(return_value=True),
            ) as should_compact_mock,
            patch(
                "app.routers.ws.CompactionService.auto_compact_if_needed",
                new=AsyncMock(
                    return_value=CompactionResult(
                        session_id=UUID(session_id),
                        raw_token_count=120,
                        compressed_token_count=40,
                        summary_preview="summary",
                    )
                ),
            ) as auto_compact_mock,
        ):
            with patch(
                "app.routers.ws.SessionNamingService.maybe_auto_rename",
                new=AsyncMock(return_value=None),
            ):
                with client.websocket_connect(f"/ws/sessions/{session_id}/stream?token={token}") as ws:
                    connected = ws.receive_json()
                    assert connected["type"] == "connected"

                    ws.send_json({"type": "message", "content": "hello"})
                    assert ws.receive_json()["type"] == "message_ack"
                    assert ws.receive_json()["type"] == "agent_thinking"
                    progress = ws.receive_json()
                    assert progress["type"] == "agent_progress"
                    assert progress["iteration"] == 1
                    assert ws.receive_json()["type"] == "text_delta"
                    assert ws.receive_json()["type"] == "done"
                    assert ws.receive_json()["type"] == "compaction_started"
                    assert ws.receive_json()["type"] == "compaction_completed"
                    assert ws.receive_json()["type"] == "compaction_resuming"
                    assert ws.receive_json()["type"] == "agent_thinking"
                    resumed_progress = ws.receive_json()
                    assert resumed_progress["type"] == "agent_progress"
                    assert resumed_progress["iteration"] == 1
                    resumed_text = ws.receive_json()
                    assert resumed_text["type"] == "text_delta"
                    assert resumed_text["delta"] == "resumed run"
                    resumed_done = ws.receive_json()
                    assert resumed_done["type"] == "done"
                    assert resumed_done["stop_reason"] == "stop"

        assert fake_loop.provider.calls == 2
        should_compact_mock.assert_awaited_once()
        auto_compact_mock.assert_awaited_once()
    finally:
        settings.compaction_auto_resume_enabled = old_auto_resume
        app.dependency_overrides.clear()
        app_main.init_db = old_init
        if "old_agent_runtime_support" in locals():
            app.state.agent_runtime_support = old_agent_runtime_support
