import os
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from tests.fake_db import FakeDB

_TEST_SESSION_ID = "00000000-0000-0000-0000-000000000001"


def test_runtime_live_view_requires_auth():
    client = TestClient(app)
    response = client.get("/api/v1/runtime/live-view", params={"session_id": _TEST_SESSION_ID})
    assert response.status_code == 401


def test_runtime_live_view_payload(monkeypatch):
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

    monkeypatch.setattr(
        "app.routers.runtime.is_runtime_available_for_session",
        lambda session_id: True,
    )

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.get(
            "/api/v1/runtime/live-view",
            headers=headers,
            params={"session_id": _TEST_SESSION_ID},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["enabled"] is True
        assert payload["available"] is True
        assert "/vnc/" in payload["url"]
        assert _TEST_SESSION_ID in payload["url"]
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_live_view_uses_origin_header(monkeypatch):
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

    monkeypatch.setattr(
        "app.routers.runtime.is_runtime_available_for_session",
        lambda session_id: True,
    )

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}",
            "Origin": "http://localhost:4747",
        }

        response = client.get(
            "/api/v1/runtime/live-view",
            headers=headers,
            params={"session_id": _TEST_SESSION_ID},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["url"].startswith(f"http://localhost:4747/vnc/{_TEST_SESSION_ID}/vnc.html")
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_live_view_uses_referer_when_origin_missing(monkeypatch):
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

    monkeypatch.setattr(
        "app.routers.runtime.is_runtime_available_for_session",
        lambda session_id: True,
    )

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}",
            "Referer": "http://localhost:4747/sentinel/sessions",
        }

        response = client.get(
            "/api/v1/runtime/live-view",
            headers=headers,
            params={"session_id": _TEST_SESSION_ID},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["url"].startswith(f"http://localhost:4747/vnc/{_TEST_SESSION_ID}/vnc.html")
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_reset_browser_endpoint(monkeypatch):
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    class _StubPool:
        async def get(self, session_id):
            return _StubManager()

    class _StubManager:
        async def reset(self):
            return {
                "reset": True,
                "url": "about:blank",
                "profile_dir": "/data/browser-profile",
                "stale_lock_cleared": True,
            }

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr(
        "app.routers.runtime._resolve_browser_pool", lambda request: _StubPool()
    )

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.post(
            "/api/v1/runtime/reset",
            headers=headers,
            params={"session_id": _TEST_SESSION_ID},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["reset"] is True
        assert payload["url"] == "about:blank"
        assert payload["stale_lock_cleared"] is True
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
