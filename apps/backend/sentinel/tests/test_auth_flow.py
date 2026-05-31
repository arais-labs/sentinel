"""JWT login + revocation flow tests.

These tests cover the auth lifecycle (login, token usage, revocation) and the
admin-credential seeding path in `ensure_default_auth_settings`. They hit
`/api/v1/instances/main/sessions` only to exercise an authenticated route — a
`main` instance is *not* created. The `get_db` override yields a `FakeDB` and
short-circuits `get_instance_record`, so instance-scoped routing is not
exercised here.

End-to-end instance routing is covered by:
  - tests/test_instances_api.py        (CRUD against the registry router)
  - tests/test_instance_route_mounts.py (verifies all instance-scoped mounts)
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.dependencies import get_db, get_manager_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.services.auth_service import (
    auth_is_configured,
    authenticate_user,
    bootstrap_auth_settings,
    ensure_default_auth_settings,
)
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

        token_resp = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert token_resp.status_code == 200
        access_token = token_resp.json()["access_token"]

        sessions_resp = client.get(
            "/api/v1/instances/main/sessions", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert sessions_resp.status_code == 200

        revoke_resp = client.delete(
            "/api/v1/auth/session", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert revoke_resp.status_code == 200

        revoked_resp = client.get(
            "/api/v1/instances/main/sessions", headers={"Authorization": f"Bearer {access_token}"}
        )
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

        sessions_resp = client.get(
            "/api/v1/instances/main/sessions", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert sessions_resp.status_code == 200

        revoke_resp = client.delete(
            "/api/v1/auth/session", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert revoke_resp.status_code == 200

        revoked_resp = client.get(
            "/api/v1/instances/main/sessions", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert revoked_resp.status_code == 401
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


@pytest.mark.asyncio
async def test_auth_settings_sync_to_configured_env_credentials(monkeypatch):
    fake_db = FakeDB(seed_auth=False)
    await ensure_default_auth_settings(fake_db)

    assert await authenticate_user(fake_db, username="admin", password="admin") == (
        "admin",
        "admin",
    )

    monkeypatch.setattr(settings, "sentinel_auth_username", "owner")
    monkeypatch.setattr(settings, "sentinel_auth_password", "new-secret")

    await ensure_default_auth_settings(fake_db)

    assert await authenticate_user(fake_db, username="admin", password="admin") is None
    assert await authenticate_user(fake_db, username="owner", password="new-secret") == (
        "owner",
        "admin",
    )


@pytest.mark.asyncio
async def test_desktop_mode_db_wins_over_env(monkeypatch):
    """In desktop mode, DB credentials must survive env changes across restarts."""
    monkeypatch.setattr(settings, "app_env", "desktop")
    fake_db = FakeDB(seed_auth=False)

    # First boot: env seeds the DB.
    monkeypatch.setattr(settings, "sentinel_auth_username", "operator")
    monkeypatch.setattr(settings, "sentinel_auth_password", "first-pw")
    await ensure_default_auth_settings(fake_db)
    assert await authenticate_user(fake_db, username="operator", password="first-pw") == (
        "operator",
        "admin",
    )

    # Simulate the user changing the password in-app.
    from app.services.auth_service import change_user_password

    assert await change_user_password(
        fake_db, username="operator", current_password="first-pw", new_password="rotated-pw"
    )

    # Restart with a different env value — DB must still win.
    monkeypatch.setattr(settings, "sentinel_auth_password", "stale-env-value")
    await ensure_default_auth_settings(fake_db)

    assert await authenticate_user(fake_db, username="operator", password="rotated-pw") == (
        "operator",
        "admin",
    )
    assert await authenticate_user(fake_db, username="operator", password="stale-env-value") is None


@pytest.mark.asyncio
async def test_desktop_mode_without_seed_waits_for_bootstrap(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "desktop")
    monkeypatch.setattr(settings, "sentinel_auth_username", "")
    monkeypatch.setattr(settings, "sentinel_auth_password", "")
    fake_db = FakeDB(seed_auth=False)

    await ensure_default_auth_settings(fake_db)
    assert await auth_is_configured(fake_db) is False

    assert await bootstrap_auth_settings(fake_db, username="Owner", password="local-secret")
    assert await auth_is_configured(fake_db) is True
    assert await authenticate_user(fake_db, username="owner", password="local-secret") == (
        "owner",
        "admin",
    )
    assert not await bootstrap_auth_settings(fake_db, username="other", password="other-secret")


def test_desktop_bootstrap_endpoint_creates_first_admin(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "desktop")
    monkeypatch.setattr(settings, "sentinel_auth_username", "")
    monkeypatch.setattr(settings, "sentinel_auth_password", "")
    fake_db = FakeDB(seed_auth=False)

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

        status_resp = client.get("/api/v1/auth/status")
        assert status_resp.status_code == 200
        assert status_resp.json() == {"configured": False, "bootstrap_available": True}

        bootstrap_resp = client.post(
            "/api/v1/auth/bootstrap",
            json={"username": "Owner", "password": "local-secret"},
        )
        assert bootstrap_resp.status_code == 200
        assert bootstrap_resp.json()["access_token"]

        status_after = client.get("/api/v1/auth/status")
        assert status_after.status_code == 200
        assert status_after.json() == {"configured": True, "bootstrap_available": False}

        login_resp = client.post(
            "/api/v1/auth/login", json={"username": "owner", "password": "local-secret"}
        )
        assert login_resp.status_code == 200

        second_bootstrap = client.post(
            "/api/v1/auth/bootstrap",
            json={"username": "other", "password": "other-secret"},
        )
        assert second_bootstrap.status_code == 409
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


@pytest.mark.asyncio
async def test_server_mode_requires_env_credentials(monkeypatch):
    """Outside desktop mode, missing env credentials raise at startup."""
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "sentinel_auth_username", "")
    monkeypatch.setattr(settings, "sentinel_auth_password", "")
    fake_db = FakeDB(seed_auth=False)

    with pytest.raises(RuntimeError, match="SENTINEL_AUTH_USERNAME"):
        await ensure_default_auth_settings(fake_db)
