import os
import uuid

import jwt
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("DEV_TOKEN", "sentinel-dev-token")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import Session
from tests.fake_db import FakeDB


def _make_token(*, sub: str, role: str = "agent", agent_id: str = "agent-test") -> str:
    secret = os.getenv("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
    return jwt.encode(
        {
            "sub": sub,
            "role": role,
            "agent_id": agent_id,
            "exp": 1999999999,
            "iat": 1771810000,
            "jti": str(uuid.uuid4()),
            "token_type": "access",
        },
        secret,
        algorithm="HS256",
    )


def test_sessions_crud_and_ownership():
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
        user1_token_resp = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        assert user1_token_resp.status_code == 200
        user1_token = user1_token_resp.json()["access_token"]

        user2_token = _make_token(sub="other-user")

        s1 = client.post("/api/v1/sessions", json={"title": "alpha"}, headers={"Authorization": f"Bearer {user1_token}"})
        s2 = client.post("/api/v1/sessions", json={"title": "beta"}, headers={"Authorization": f"Bearer {user1_token}"})
        s_child = client.post(
            "/api/v1/sessions",
            json={"title": "sub-agent:child"},
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        s3 = client.post("/api/v1/sessions", json={"title": "gamma"}, headers={"Authorization": f"Bearer {user2_token}"})
        assert s1.status_code == 200 and s2.status_code == 200 and s3.status_code == 200 and s_child.status_code == 200

        session1_id = s1.json()["id"]
        session2_id = s2.json()["id"]
        child_session_id = s_child.json()["id"]
        session3_id = s3.json()["id"]

        # Mark one session as a child run (sub-agent session) and ensure it is hidden from top-level listing.
        for item in fake_db.storage[Session]:
            if str(item.id) == child_session_id:
                item.parent_session_id = uuid.UUID(session1_id)
                break

        list_user1 = client.get("/api/v1/sessions", headers={"Authorization": f"Bearer {user1_token}"})
        assert list_user1.status_code == 200
        ids_user1 = {item["id"] for item in list_user1.json()["items"]}
        assert session1_id in ids_user1
        assert session2_id in ids_user1
        assert child_session_id not in ids_user1
        assert session3_id not in ids_user1

        forbidden_get = client.get(f"/api/v1/sessions/{session3_id}", headers={"Authorization": f"Bearer {user1_token}"})
        assert forbidden_get.status_code == 404

        end_resp = client.delete(f"/api/v1/sessions/{session1_id}", headers={"Authorization": f"Bearer {user1_token}"})
        assert end_resp.status_code == 200
        assert end_resp.json()["status"] == "ended"
        ended_session = client.get(f"/api/v1/sessions/{session1_id}", headers={"Authorization": f"Bearer {user1_token}"})
        assert ended_session.status_code == 200
        assert ended_session.json()["status"] == "ended"

        m1 = client.post(
            f"/api/v1/sessions/{session2_id}/messages",
            json={"role": "user", "content": "first", "metadata": {}},
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        m2 = client.post(
            f"/api/v1/sessions/{session2_id}/messages",
            json={"role": "system", "content": "second", "metadata": {}},
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        m3 = client.post(
            f"/api/v1/sessions/{session2_id}/messages",
            json={"role": "user", "content": "third", "metadata": {}},
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert m1.status_code == 200 and m2.status_code == 200 and m3.status_code == 200

        history = client.get(
            f"/api/v1/sessions/{session2_id}/messages?limit=2",
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert history.status_code == 200
        payload = history.json()
        assert len(payload["items"]) == 2
        assert payload["has_more"] is True

        stop_resp = client.post(
            f"/api/v1/sessions/{session2_id}/stop",
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] in {"stopping", "idle"}
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
