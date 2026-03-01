import os
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from tests.fake_db import FakeDB

def test_trigger_type_and_action_type_update():
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
        token_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert token_resp.status_code == 200
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Create a cron trigger
        create = client.post(
            "/api/v1/triggers",
            json={
                "name": "cron-trigger",
                "type": "cron",
                "config": {"expr": "*/5 * * * *"},
                "action_type": "agent_message",
                "action_config": {"message": "ping"},
            },
            headers=headers,
        )
        assert create.status_code == 200
        trigger_id = create.json()["id"]
        assert create.json()["type"] == "cron"
        assert create.json()["action_type"] == "agent_message"
        initial_next_fire = create.json()["next_fire_at"]
        assert initial_next_fire is not None

        # 2. Update type from cron to heartbeat
        update_type = client.patch(
            f"/api/v1/triggers/{trigger_id}",
            json={
                "type": "heartbeat",
                "config": {"interval_seconds": 60}
            },
            headers=headers,
        )
        assert update_type.status_code == 200
        data = update_type.json()
        assert data["type"] == "heartbeat"
        assert data["config"]["interval_seconds"] == 60
        assert data["next_fire_at"] is not None

        # 3. Update action_type from agent_message to http_request
        update_action = client.patch(
            f"/api/v1/triggers/{trigger_id}",
            json={
                "action_type": "http_request",
                "action_config": {"url": "https://example.com/hook", "method": "POST"}
            },
            headers=headers,
        )
        assert update_action.status_code == 200
        data = update_action.json()
        assert data["action_type"] == "http_request"
        assert data["action_config"]["url"] == "https://example.com/hook"

        # 4. Update action_config WITHOUT changing action_type
        update_config = client.patch(
            f"/api/v1/triggers/{trigger_id}",
            json={
                "action_config": {"url": "https://example.com/new-hook", "method": "GET"}
            },
            headers=headers,
        )
        assert update_config.status_code == 200
        data = update_config.json()
        assert data["action_type"] == "http_request"
        assert data["action_config"]["url"] == "https://example.com/new-hook"
        assert data["action_config"]["method"] == "GET"

        # 5. Verify partial update (only name)
        update_name = client.patch(
            f"/api/v1/triggers/{trigger_id}",
            json={"name": "renamed-trigger"},
            headers=headers,
        )
        assert update_name.status_code == 200
        data = update_name.json()
        assert data["name"] == "renamed-trigger"
        assert data["type"] == "heartbeat" # preserved
        assert data["action_type"] == "http_request" # preserved

    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
