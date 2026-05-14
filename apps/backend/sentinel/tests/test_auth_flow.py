import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.dependencies import get_db, get_manager_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.services.auth_service import authenticate_user, ensure_default_auth_settings
from tests.fake_db import FakeDB


def test_login_use_revoke_flow():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _override_get_manager_db():
        yield fake_db

    async def _noop_init_db():
        return None

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_manager_db
    from app import main as app_main
    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()

    try:
        client = TestClient(app)

        token_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert token_resp.status_code == 200
        access_token = token_resp.json()["access_token"]

        sessions_resp = client.get("/api/v1/instances/main/sessions", headers={"Authorization": f"Bearer {access_token}"})
        assert sessions_resp.status_code == 200

        revoke_resp = client.delete("/api/v1/auth/session", headers={"Authorization": f"Bearer {access_token}"})
        assert revoke_resp.status_code == 200

        revoked_resp = client.get("/api/v1/instances/main/sessions", headers={"Authorization": f"Bearer {access_token}"})
        assert revoked_resp.status_code == 401
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init

def test_login_accepts_seeded_credentials_and_can_revoke():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _override_get_manager_db():
        yield fake_db

    async def _noop_init_db():
        return None

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_manager_db
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

        sessions_resp = client.get("/api/v1/instances/main/sessions", headers={"Authorization": f"Bearer {access_token}"})
        assert sessions_resp.status_code == 200

        revoke_resp = client.delete("/api/v1/auth/session", headers={"Authorization": f"Bearer {access_token}"})
        assert revoke_resp.status_code == 200

        revoked_resp = client.get("/api/v1/instances/main/sessions", headers={"Authorization": f"Bearer {access_token}"})
        assert revoked_resp.status_code == 401
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


@pytest.mark.asyncio
async def test_auth_settings_sync_to_configured_env_credentials(monkeypatch):
    fake_db = FakeDB(seed_auth=False)
    await ensure_default_auth_settings(fake_db)

    assert await authenticate_user(fake_db, username="admin", password="admin") == ("admin", "admin")

    monkeypatch.setattr(settings, "sentinel_auth_username", "owner")
    monkeypatch.setattr(settings, "sentinel_auth_password", "new-secret")

    await ensure_default_auth_settings(fake_db)

    assert await authenticate_user(fake_db, username="admin", password="admin") is None
    assert await authenticate_user(fake_db, username="owner", password="new-secret") == ("owner", "admin")
