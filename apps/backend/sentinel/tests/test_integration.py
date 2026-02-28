import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("DEV_TOKEN", "sentinel-dev-token")
os.environ.setdefault("TOOL_FILE_READ_BASE_DIR", "/tmp")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from tests.fake_db import FakeDB


def test_full_integration_happy_path():
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

        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        created_session = client.post(
            "/api/v1/sessions", json={"title": "integration-e2e"}, headers=headers
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        for i in range(12):
            sent = client.post(
                f"/api/v1/sessions/{session_id}/messages",
                json={
                    "role": "user" if i % 2 == 0 else "system",
                    "content": f"integration message {i} " + " ".join(["detail"] * 12),
                    "metadata": {},
                },
                headers=headers,
            )
            assert sent.status_code == 200

        compacted = client.post(f"/api/v1/sessions/{session_id}/compact", headers=headers)
        assert compacted.status_code == 200
        assert compacted.json()["raw_token_count"] > compacted.json()["compressed_token_count"]

        spawned = client.post(
            f"/api/v1/sessions/{session_id}/sub-agents",
            json={"name": "triage blockers", "scope": "recent messages", "max_steps": 4},
            headers=headers,
        )
        assert spawned.status_code == 202
        task_id = spawned.json()["id"]

        task_list = client.get(f"/api/v1/sessions/{session_id}/sub-agents", headers=headers)
        assert task_list.status_code == 200
        assert any(item["id"] == task_id for item in task_list.json()["items"])

        cancelled = client.delete(
            f"/api/v1/sessions/{session_id}/sub-agents/{task_id}", headers=headers
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        trigger = client.post(
            "/api/v1/triggers",
            json={
                "name": "integration-trigger",
                "type": "cron",
                "config": {"cron": "*/15 * * * *"},
                "action_type": "agent_message",
                "action_config": {"message": "run"},
            },
            headers=headers,
        )
        assert trigger.status_code == 200
        trigger_id = trigger.json()["id"]

        fired = client.post(
            f"/api/v1/triggers/{trigger_id}/fire",
            json={"input_payload": {"source": "integration"}},
            headers=headers,
        )
        assert fired.status_code == 200

        read_base_dir = Path(os.getenv("TOOL_FILE_READ_BASE_DIR", "/tmp")).resolve()
        with tempfile.NamedTemporaryFile("w", delete=False, dir=read_base_dir) as handle:
            handle.write("integration tool check")
            file_path = handle.name

        tools = client.get("/api/v1/tools", headers=headers)
        assert tools.status_code == 200
        tool_names = {item["name"] for item in tools.json()["items"]}
        assert {"file_read", "shell_exec"} <= tool_names

        file_read = client.post(
            "/api/v1/tools/file_read/execute",
            json={"input": {"path": file_path}},
            headers=headers,
        )
        assert file_read.status_code == 200
        assert "integration tool check" in file_read.json()["result"]["content"]

        live_view = client.get("/api/v1/playwright/live-view", headers=headers)
        assert live_view.status_code == 200
        assert "enabled" in live_view.json()

        with client.websocket_connect(f"/ws/sessions/{session_id}/stream?token={token}") as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            ws.send_json({"type": "message", "content": "integration websocket message"})
            ack = ws.receive_json()
            assert ack["type"] == "message_ack"
            assert ack["content"] == "integration websocket message"

        estop = client.post("/api/v1/admin/estop", headers=headers)
        assert estop.status_code == 200

        blocked_shell = client.post(
            "/api/v1/tools/shell_exec/execute",
            json={"input": {"command": "echo blocked"}},
            headers=headers,
        )
        assert blocked_shell.status_code == 403

        config = client.get("/api/v1/admin/config", headers=headers)
        assert config.status_code == 200

        audits = client.get("/api/v1/admin/audit", headers=headers)
        assert audits.status_code == 200
        assert audits.json()["total"] >= 2
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
