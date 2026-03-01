import os

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.services.llm.generic.types import TokenUsage
from tests.fake_db import FakeDB


class _FakeLoop:
    def __init__(self) -> None:
        self.calls = []

    async def run(self, db, session_id, user_message, **kwargs):
        self.calls.append({"session_id": session_id, "user_message": user_message, **kwargs})

        class _Result:
            final_text = "Agent response"
            iterations = 2
            usage = TokenUsage(input_tokens=11, output_tokens=7)

        return _Result()


def test_chat_endpoint_calls_agent_loop_and_returns_response():
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
        old_agent_loop = getattr(app.state, "agent_loop", None)
        app.state.agent_loop = fake_loop
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "chat"}, headers=headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        chat = client.post(
            f"/api/v1/sessions/{session_id}/chat",
            json={"content": "hello model", "tier": "normal"},
            headers=headers,
        )
        assert chat.status_code == 200
        payload = chat.json()
        assert payload["response"] == "Agent response"
        assert payload["iterations"] == 2
        assert payload["usage"] == {"input_tokens": 11, "output_tokens": 7}
        assert fake_loop.calls[0]["user_message"] == "hello model"
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
        if "old_agent_loop" in locals():
            app.state.agent_loop = old_agent_loop


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
        old_agent_loop = getattr(app.state, "agent_loop", None)
        app.state.agent_loop = None
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
        if "old_agent_loop" in locals():
            app.state.agent_loop = old_agent_loop
