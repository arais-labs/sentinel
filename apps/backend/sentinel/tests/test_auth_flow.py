import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from fastapi.testclient import TestClient

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from tests.fake_db import FakeDB


def test_login_use_revoke_flow():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    app.dependency_overrides[get_db] = _override_get_db
    from app import main as app_main
    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()

    try:
        client = TestClient(app)

        token_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert token_resp.status_code == 200
        access_token = token_resp.json()["access_token"]

        sessions_resp = client.get("/api/v1/sessions", headers={"Authorization": f"Bearer {access_token}"})
        assert sessions_resp.status_code == 200

        revoke_resp = client.delete("/api/v1/auth/session", headers={"Authorization": f"Bearer {access_token}"})
        assert revoke_resp.status_code == 200

        revoked_resp = client.get("/api/v1/sessions", headers={"Authorization": f"Bearer {access_token}"})
        assert revoked_resp.status_code == 401
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init

def test_login_accepts_seeded_credentials_and_can_revoke():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    app.dependency_overrides[get_db] = _override_get_db
    from app import main as app_main
    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()

    try:
        client = TestClient(app)

        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin"},
        )
        assert login_resp.status_code == 200
        access_token = login_resp.json()["access_token"]

        sessions_resp = client.get("/api/v1/sessions", headers={"Authorization": f"Bearer {access_token}"})
        assert sessions_resp.status_code == 200

        revoke_resp = client.delete("/api/v1/auth/session", headers={"Authorization": f"Bearer {access_token}"})
        assert revoke_resp.status_code == 200

        revoked_resp = client.get("/api/v1/sessions", headers={"Authorization": f"Bearer {access_token}"})
        assert revoked_resp.status_code == 401
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
