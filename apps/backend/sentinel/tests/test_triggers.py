import hashlib
import hmac
import os
import uuid

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("DEV_TOKEN", "sentinel-dev-token")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from tests.fake_db import FakeDB


def test_triggers_crud_fire_logs_and_webhook():
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
        token_resp = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        assert token_resp.status_code == 200
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        invalid = client.post(
            "/api/v1/triggers",
            json={
                "name": "invalid",
                "type": "invalid",
                "config": {},
                "action_type": "agent_message",
                "action_config": {},
            },
            headers=headers,
        )
        assert invalid.status_code == 422

        create = client.post(
            "/api/v1/triggers",
            json={
                "name": "daily-check",
                "type": "cron",
                "config": {"cron": "*/5 * * * *"},
                "action_type": "agent_message",
                "action_config": {"message": "ping"},
            },
            headers=headers,
        )
        assert create.status_code == 200
        assert create.json()["next_fire_at"] is not None
        trigger_id = create.json()["id"]

        heartbeat = client.post(
            "/api/v1/triggers",
            json={
                "name": "heartbeat-check",
                "type": "heartbeat",
                "config": {"interval_seconds": 30},
                "action_type": "agent_message",
                "action_config": {"message": "pulse"},
            },
            headers=headers,
        )
        assert heartbeat.status_code == 200
        assert heartbeat.json()["next_fire_at"] is not None

        listed = client.get("/api/v1/triggers", headers=headers)
        assert listed.status_code == 200
        assert any(item["id"] == trigger_id for item in listed.json()["items"])

        fetched = client.get(f"/api/v1/triggers/{trigger_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "daily-check"

        updated = client.patch(
            f"/api/v1/triggers/{trigger_id}",
            json={"name": "daily-check-updated"},
            headers=headers,
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "daily-check-updated"

        fetched_after_update = client.get(f"/api/v1/triggers/{trigger_id}", headers=headers)
        assert fetched_after_update.status_code == 200
        assert fetched_after_update.json()["name"] == "daily-check-updated"

        fired = client.post(
            f"/api/v1/triggers/{trigger_id}/fire",
            json={"input_payload": {"source": "manual"}},
            headers=headers,
        )
        assert fired.status_code == 200
        fire_log_id = fired.json()["id"]

        logs = client.get(f"/api/v1/triggers/{trigger_id}/logs", headers=headers)
        assert logs.status_code == 200
        assert any(item["id"] == fire_log_id for item in logs.json()["items"])

        deleted = client.delete(f"/api/v1/triggers/{trigger_id}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "deleted"

        after_delete = client.get(f"/api/v1/triggers/{trigger_id}", headers=headers)
        assert after_delete.status_code == 404

        webhook = client.post(
            "/api/v1/triggers",
            json={
                "name": "incoming-event",
                "type": "webhook",
                "config": {"secret": "topsecret"},
                "action_type": "agent_message",
                "action_config": {"message": "webhook fired"},
            },
            headers=headers,
        )
        assert webhook.status_code == 200
        webhook_id = webhook.json()["id"]

        payload = b'{"event":"ping"}'
        valid_sig = hmac.new(b"topsecret", payload, hashlib.sha256).hexdigest()
        accepted = client.post(
            f"/api/v1/webhooks/{webhook_id}",
            content=payload,
            headers={"X-Webhook-Signature": valid_sig, "Content-Type": "application/json"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "accepted"

        rejected = client.post(
            f"/api/v1/webhooks/{webhook_id}",
            content=payload,
            headers={"X-Webhook-Signature": "bad-signature", "Content-Type": "application/json"},
        )
        assert rejected.status_code == 401

        missing = client.post(
            f"/api/v1/webhooks/{uuid.uuid4()}",
            content=payload,
            headers={"X-Webhook-Signature": valid_sig, "Content-Type": "application/json"},
        )
        assert missing.status_code == 404

        disabled = client.post(
            "/api/v1/triggers",
            json={
                "name": "disabled-webhook",
                "type": "webhook",
                "enabled": False,
                "config": {"secret": "topsecret"},
                "action_type": "agent_message",
                "action_config": {"message": "off"},
            },
            headers=headers,
        )
        assert disabled.status_code == 200
        disabled_id = disabled.json()["id"]
        disabled_resp = client.post(
            f"/api/v1/webhooks/{disabled_id}",
            content=payload,
            headers={"X-Webhook-Signature": valid_sig, "Content-Type": "application/json"},
        )
        assert disabled_resp.status_code == 409
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
