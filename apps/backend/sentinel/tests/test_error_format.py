import os
import uuid

import jwt
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
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


def test_error_response_format_consistency():
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

        unauthorized = client.get("/api/v1/sessions")
        assert unauthorized.status_code == 401
        assert unauthorized.json()["error"]["code"] == "unauthorized"

        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        not_found = client.get(f"/api/v1/sessions/{uuid.uuid4()}", headers=headers)
        assert not_found.status_code == 404
        assert not_found.json()["error"]["code"] == "not_found"

        session = client.post("/api/v1/sessions", json={"title": "err-test"}, headers=headers)
        assert session.status_code == 200
        session_id = session.json()["id"]

        validation = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"role": "user", "content": "   ", "metadata": {}},
            headers=headers,
        )
        assert validation.status_code == 422
        assert validation.json()["error"]["code"] == "validation_error"

        estop = client.post("/api/v1/admin/estop", headers=headers)
        assert estop.status_code == 200

        non_admin_headers = {"Authorization": f"Bearer {_make_token(sub='standard-user', role='agent')}"}
        forbidden = client.get("/api/v1/admin/config", headers=non_admin_headers)
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "forbidden"

        RateLimitMiddleware._buckets.clear()
        last = None
        for _ in range(11):
            last = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert last is not None
        assert last.status_code == 429
        assert last.json()["error"]["code"] == "rate_limited"
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
