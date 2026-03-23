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
from app.services.sessions.compaction import CompactionResult
from app.services.llm.generic.types import AgentEvent
from tests.fake_db import FakeDB


class _FakeLoop:
    def __init__(self, deltas_by_run: list[list[str]] | None = None) -> None:
        self._deltas_by_run = deltas_by_run or [["agent ", "says hi"]]
        self.calls = 0

    async def run(self, db, session_id, user_message, **kwargs):
        run_idx = min(self.calls, len(self._deltas_by_run) - 1)
        self.calls += 1
        callback = kwargs.get("on_event")
        if callback:
            for delta in self._deltas_by_run[run_idx]:
                await callback(AgentEvent(type="text_delta", delta=delta))
            await callback(AgentEvent(type="done", stop_reason="stop"))
        return SimpleNamespace(error=None)


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
        old_agent_loop = getattr(app.state, "agent_loop", None)
        app.state.agent_loop = _FakeLoop()
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "ws-stream"}, headers=headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        with client.websocket_connect(f"/ws/sessions/{session_id}/stream?token={token}") as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"

            ws.send_json({"type": "message", "content": "hello"})
            ack = ws.receive_json()
            assert ack["type"] == "message_ack"

            thinking = ws.receive_json()
            assert thinking["type"] == "agent_thinking"

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
        if "old_agent_loop" in locals():
            app.state.agent_loop = old_agent_loop


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
        old_agent_loop = getattr(app.state, "agent_loop", None)
        fake_loop = _FakeLoop(deltas_by_run=[["first run"], ["resumed run"]])
        app.state.agent_loop = fake_loop
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
            with client.websocket_connect(f"/ws/sessions/{session_id}/stream?token={token}") as ws:
                connected = ws.receive_json()
                assert connected["type"] == "connected"

                ws.send_json({"type": "message", "content": "hello"})
                assert ws.receive_json()["type"] == "message_ack"
                assert ws.receive_json()["type"] == "agent_thinking"
                assert ws.receive_json()["type"] == "text_delta"
                assert ws.receive_json()["type"] == "done"
                assert ws.receive_json()["type"] == "compaction_started"
                assert ws.receive_json()["type"] == "compaction_completed"
                assert ws.receive_json()["type"] == "compaction_resuming"
                assert ws.receive_json()["type"] == "agent_thinking"
                resumed_text = ws.receive_json()
                assert resumed_text["type"] == "text_delta"
                assert resumed_text["delta"] == "resumed run"
                resumed_done = ws.receive_json()
                assert resumed_done["type"] == "done"
                assert resumed_done["stop_reason"] == "stop"

        assert fake_loop.calls == 2
        should_compact_mock.assert_awaited_once()
        auto_compact_mock.assert_awaited_once()
    finally:
        settings.compaction_auto_resume_enabled = old_auto_resume
        app.dependency_overrides.clear()
        app_main.init_db = old_init
        if "old_agent_loop" in locals():
            app.state.agent_loop = old_agent_loop
