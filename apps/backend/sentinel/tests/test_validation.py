import os

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("DEV_TOKEN", "sentinel-dev-token")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from tests.fake_db import FakeDB


def test_validation_hardening_rules():
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
        login = client.post("/api/v1/auth/token", json={"araios_token": "  sentinel-dev-token  "})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        too_long_title = "x" * 201
        invalid_session = client.post(
            "/api/v1/sessions", json={"title": too_long_title}, headers=headers
        )
        assert invalid_session.status_code == 422

        valid_session = client.post(
            "/api/v1/sessions", json={"title": "  Trimmed Title  "}, headers=headers
        )
        assert valid_session.status_code == 200
        session_id = valid_session.json()["id"]
        assert valid_session.json()["title"] == "Trimmed Title"

        empty_message = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"role": "user", "content": "   ", "metadata": {}},
            headers=headers,
        )
        assert empty_message.status_code == 422

        too_long_message = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"role": "user", "content": "m" * 50_001, "metadata": {}},
            headers=headers,
        )
        assert too_long_message.status_code == 422

        empty_memory = client.post(
            "/api/v1/memory",
            json={"content": "   ", "category": "core", "metadata": {}},
            headers=headers,
        )
        assert empty_memory.status_code == 422

        too_long_memory = client.post(
            "/api/v1/memory",
            json={"content": "m" * 50_001, "category": "core", "metadata": {}},
            headers=headers,
        )
        assert too_long_memory.status_code == 422

        invalid_trigger_name = client.post(
            "/api/v1/triggers",
            json={
                "name": "   ",
                "type": "cron",
                "config": {},
                "action_type": "agent_message",
                "action_config": {},
            },
            headers=headers,
        )
        assert invalid_trigger_name.status_code == 422

        deep_config = {"a": {"b": {"c": {"d": {"e": {"f": "too-deep"}}}}}}
        invalid_trigger_depth = client.post(
            "/api/v1/triggers",
            json={
                "name": "depth-check",
                "type": "cron",
                "config": deep_config,
                "action_type": "agent_message",
                "action_config": {},
            },
            headers=headers,
        )
        assert invalid_trigger_depth.status_code == 422

        long_sub_agent_name = "s" * 201
        invalid_sub_agent = client.post(
            f"/api/v1/sessions/{session_id}/sub-agents",
            json={"name": long_sub_agent_name, "scope": "x", "max_steps": 2},
            headers=headers,
        )
        assert invalid_sub_agent.status_code == 422
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
