import hashlib
import hmac
import os
import uuid

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.main import app
from tests.fake_db import FakeDB
from tests.helpers import install_fake_db_overrides, make_fake_instance_context, restore_test_app

TRIGGERS_API = "/api/v1/instances/main/triggers"
WEBHOOKS_API = "/api/v1/instances/main/webhooks"


def test_triggers_crud_fire_logs_and_webhook():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(
        app_db=fake_db,
        instance_context=make_fake_instance_context(app_db=fake_db),
    )

    try:
        client = TestClient(app)
        token_resp = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert token_resp.status_code == 200
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        session_resp = client.post(
            "/api/v1/instances/main/sessions", json={"title": "trigger-target"}, headers=headers
        )
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        invalid = client.post(
            TRIGGERS_API,
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
            TRIGGERS_API,
            json={
                "name": "daily-check",
                "type": "cron",
                "config": {"cron": "*/5 * * * *"},
                "action_type": "agent_message",
                "action_config": {"message": "ping", "target_session_id": session_id},
            },
            headers=headers,
        )
        assert create.status_code == 200
        assert create.json()["next_fire_at"] is not None
        trigger_id = create.json()["id"]

        heartbeat = client.post(
            TRIGGERS_API,
            json={
                "name": "heartbeat-check",
                "type": "heartbeat",
                "config": {"interval_seconds": 30},
                "action_type": "agent_message",
                "action_config": {"message": "pulse", "target_session_id": session_id},
            },
            headers=headers,
        )
        assert heartbeat.status_code == 200
        assert heartbeat.json()["next_fire_at"] is not None

        listed = client.get(TRIGGERS_API, headers=headers)
        assert listed.status_code == 200
        assert any(item["id"] == trigger_id for item in listed.json()["items"])

        fetched = client.get(f"{TRIGGERS_API}/{trigger_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "daily-check"

        updated = client.patch(
            f"{TRIGGERS_API}/{trigger_id}",
            json={"name": "daily-check-updated"},
            headers=headers,
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "daily-check-updated"

        fetched_after_update = client.get(f"{TRIGGERS_API}/{trigger_id}", headers=headers)
        assert fetched_after_update.status_code == 200
        assert fetched_after_update.json()["name"] == "daily-check-updated"

        fired = client.post(
            f"{TRIGGERS_API}/{trigger_id}/fire",
            json={"input_payload": {"source": "manual"}},
            headers=headers,
        )
        assert fired.status_code == 200
        assert fired.json()["log"]["input_payload"] == {"source": "manual"}
        fire_log_id = fired.json()["log"]["id"]

        logs = client.get(f"{TRIGGERS_API}/{trigger_id}/logs", headers=headers)
        assert logs.status_code == 200
        assert any(item["id"] == fire_log_id for item in logs.json()["items"])

        deleted = client.delete(f"{TRIGGERS_API}/{trigger_id}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "deleted"

        after_delete = client.get(f"{TRIGGERS_API}/{trigger_id}", headers=headers)
        assert after_delete.status_code == 404

        webhook = client.post(
            TRIGGERS_API,
            json={
                "name": "incoming-event",
                "type": "webhook",
                "config": {"secret": "topsecret"},
                "action_type": "agent_message",
                "action_config": {"message": "webhook fired", "target_session_id": session_id},
            },
            headers=headers,
        )
        assert webhook.status_code == 200
        webhook_id = webhook.json()["id"]

        payload = b'{"event":"ping"}'
        valid_sig = hmac.new(b"topsecret", payload, hashlib.sha256).hexdigest()
        accepted = client.post(
            f"{WEBHOOKS_API}/{webhook_id}",
            content=payload,
            headers={"X-Webhook-Signature": valid_sig, "Content-Type": "application/json"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "accepted"

        rejected = client.post(
            f"{WEBHOOKS_API}/{webhook_id}",
            content=payload,
            headers={"X-Webhook-Signature": "bad-signature", "Content-Type": "application/json"},
        )
        assert rejected.status_code == 401

        missing = client.post(
            f"{WEBHOOKS_API}/{uuid.uuid4()}",
            content=payload,
            headers={"X-Webhook-Signature": valid_sig, "Content-Type": "application/json"},
        )
        assert missing.status_code == 404

        disabled = client.post(
            TRIGGERS_API,
            json={
                "name": "disabled-webhook",
                "type": "webhook",
                "enabled": False,
                "config": {"secret": "topsecret"},
                "action_type": "agent_message",
                "action_config": {"message": "off", "target_session_id": session_id},
            },
            headers=headers,
        )
        assert disabled.status_code == 200
        disabled_id = disabled.json()["id"]
        disabled_resp = client.post(
            f"{WEBHOOKS_API}/{disabled_id}",
            content=payload,
            headers={"X-Webhook-Signature": valid_sig, "Content-Type": "application/json"},
        )
        assert disabled_resp.status_code == 409
    finally:
        restore_test_app(old_init)
