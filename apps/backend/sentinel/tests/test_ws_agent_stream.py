import os
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("DEV_TOKEN", "sentinel-dev-token")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.services.llm.types import AgentEvent
from tests.fake_db import FakeDB


class _FakeLoop:
    async def run(self, db, session_id, user_message, **kwargs):
        callback = kwargs.get("on_event")
        if callback:
            await callback(AgentEvent(type="text_delta", delta="agent "))
            await callback(AgentEvent(type="text_delta", delta="says hi"))
            await callback(AgentEvent(type="done", stop_reason="stop"))



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
        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
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
