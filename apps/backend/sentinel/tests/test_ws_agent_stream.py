import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.main import app
from app.routers import ws as ws_router
from app.services.agent import PreparedRuntimeTurnContext
from app.services.sessions.compaction import CompactionResult
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import AgentEvent
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRegistry
from app.services.ws.ws_stream_service import maybe_auto_compact_after_run
from tests.fake_db import FakeDB
from tests.helpers import FakeSessionFactory, install_fake_db_overrides, make_fake_instance_context, restore_test_app


SESSIONS_API = "/api/v1/instances/main/sessions"
WS_API = "/ws/instances/main/sessions"


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
        self.context_builder = SimpleNamespace(build=self._build_context)
        registry = ToolRegistry()
        self.tool_registry = registry
        self.tool_executor = ToolExecutor(registry)
        self.provider = _FakeProvider(deltas_by_run)

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
            tools=self.tool_registry.list_schemas(),
            effective_system_prompt=system_prompt,
            runtime_context_snapshot=None,
        )

    async def persist_created_messages(self, db, session_id, created, assistant_iterations, **kwargs):
        await self._persist_messages(db, session_id, created, assistant_iterations, **kwargs)

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


def test_ws_streams_runtime_events_when_provider_available():
    fake_db = FakeDB()
    fake_loop = _FakeLoop()
    instance_context = make_fake_instance_context(app_db=fake_db, agent_runtime_support=fake_loop)
    old_init = install_fake_db_overrides(app_db=fake_db, instance_context=instance_context)

    old_manager_session = ws_router.ManagerSessionLocal
    ws_router.ManagerSessionLocal = FakeSessionFactory(FakeDB())

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_resp = client.post(SESSIONS_API, json={"title": "ws-stream"}, headers=headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        with patch(
            "app.routers.ws.SessionNamingService.maybe_auto_rename",
            new=AsyncMock(return_value=None),
        ):
            with client.websocket_connect(f"{WS_API}/{session_id}/stream?token={token}") as ws:
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
        ws_router.ManagerSessionLocal = old_manager_session
        restore_test_app(old_init)


def test_ws_auto_compacts_without_resuming():
    session_id = UUID("11111111-1111-1111-1111-111111111111")

    class _Manager:
        def __init__(self):
            self.events = []
            self.thinking_count = 0

        async def broadcast(self, _session_key, event):
            self.events.append(event)

        async def broadcast_agent_thinking(self, _session_key):
            self.thinking_count += 1

    class _CompactionService:
        should_auto_compact = AsyncMock(return_value=True)
        auto_compact_if_needed = AsyncMock(
            return_value=CompactionResult(
                session_id=session_id,
                raw_token_count=120,
                compressed_token_count=40,
                summary_preview="summary",
            )
        )

        def __init__(self, provider=None):
            self.provider = provider

    async def _run():
        manager = _Manager()
        await maybe_auto_compact_after_run(
            db=FakeDB(),
            session_id=session_id,
            session_key=str(session_id),
            manager=manager,
            agent_runtime_support=SimpleNamespace(provider=object()),
            compaction_service_cls=_CompactionService,
        )
        return manager

    manager = asyncio.run(_run())

    assert [event["type"] for event in manager.events] == [
        "compaction_started",
        "compaction_completed",
    ]
    assert manager.thinking_count == 0
    _CompactionService.should_auto_compact.assert_awaited_once()
    _CompactionService.auto_compact_if_needed.assert_awaited_once()
