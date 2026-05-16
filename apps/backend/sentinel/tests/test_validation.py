import os

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.main import app
from tests.fake_db import FakeDB
from tests.helpers import install_fake_db_overrides, restore_test_app


SESSIONS_API = "/api/v1/instances/main/sessions"
MEMORY_API = "/api/v1/instances/main/memory"
TRIGGERS_API = "/api/v1/instances/main/triggers"


def test_validation_hardening_rules():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "  admin  ", "password": "  admin  "})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        too_long_title = "x" * 201
        invalid_session = client.post(
            SESSIONS_API, json={"title": too_long_title}, headers=headers
        )
        assert invalid_session.status_code == 422

        valid_session = client.post(
            SESSIONS_API, json={"title": "  Trimmed Title  "}, headers=headers
        )
        assert valid_session.status_code == 200
        session_id = valid_session.json()["id"]
        assert valid_session.json()["title"] == "Trimmed Title"

        empty_message = client.post(
            f"{SESSIONS_API}/{session_id}/messages",
            json={"role": "user", "content": "   ", "metadata": {}},
            headers=headers,
        )
        assert empty_message.status_code == 422

        too_long_message = client.post(
            f"{SESSIONS_API}/{session_id}/messages",
            json={"role": "user", "content": "m" * 50_001, "metadata": {}},
            headers=headers,
        )
        assert too_long_message.status_code == 422

        empty_memory = client.post(
            MEMORY_API,
            json={"content": "   ", "category": "core", "metadata": {}},
            headers=headers,
        )
        assert empty_memory.status_code == 422

        too_long_memory = client.post(
            MEMORY_API,
            json={"content": "m" * 50_001, "category": "core", "metadata": {}},
            headers=headers,
        )
        assert too_long_memory.status_code == 422

        invalid_trigger_name = client.post(
            TRIGGERS_API,
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
            TRIGGERS_API,
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
            f"{SESSIONS_API}/{session_id}/sub-agents",
            json={"name": long_sub_agent_name, "scope": "x", "max_steps": 2},
            headers=headers,
        )
        assert invalid_sub_agent.status_code == 422
    finally:
        restore_test_app(old_init)
