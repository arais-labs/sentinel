import os
import uuid

import jwt
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("DEV_TOKEN", "sentinel-dev-token")

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


def test_playwright_task_crud_and_estop():
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

        unauth = client.post("/api/v1/playwright/tasks", json={"url": "https://example.com"})
        assert unauth.status_code == 401

        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        created = client.post(
            "/api/v1/playwright/tasks",
            json={"url": "https://example.com", "action": "screenshot"},
            headers=headers,
        )
        assert created.status_code == 200
        created_payload = created.json()
        assert created_payload["status"] in {"pending", "running", "completed"}
        task_id = created_payload["id"]

        detail = client.get(f"/api/v1/playwright/tasks/{task_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["id"] == task_id
        assert detail.json()["url"] == "https://example.com"

        shot = client.post(f"/api/v1/playwright/tasks/{task_id}/screenshot", headers=headers)
        assert shot.status_code == 200
        assert shot.json()["screenshot_base64"]

        cancelled = client.delete(f"/api/v1/playwright/tasks/{task_id}", headers=headers)
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        estop = client.post("/api/v1/admin/estop", headers=headers)
        assert estop.status_code == 200
        blocked = client.post(
            "/api/v1/playwright/tasks",
            json={"url": "https://example.com", "action": "extract"},
            headers=headers,
        )
        assert blocked.status_code == 403
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_playwright_task_ownership():
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
        owner = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        owner_headers = {"Authorization": f"Bearer {owner.json()['access_token']}"}

        other_token = _make_token(sub="other-user")
        other_headers = {"Authorization": f"Bearer {other_token}"}

        created = client.post(
            "/api/v1/playwright/tasks",
            json={"url": "https://example.com", "action": "interact"},
            headers=owner_headers,
        )
        assert created.status_code == 200
        task_id = created.json()["id"]

        forbidden = client.get(f"/api/v1/playwright/tasks/{task_id}", headers=other_headers)
        assert forbidden.status_code == 404
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_playwright_live_view_requires_auth():
    client = TestClient(app)
    response = client.get("/api/v1/playwright/live-view")
    assert response.status_code == 401


def test_playwright_live_view_payload(monkeypatch):
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

    monkeypatch.setattr("app.routers.playwright.is_live_view_available", lambda: True)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.get("/api/v1/playwright/live-view", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["enabled"] is True
        assert payload["available"] is True
        assert payload["mode"] == "novnc"
        assert "/vnc.html" in payload["url"]
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_playwright_live_view_uses_origin_header(monkeypatch):
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

    monkeypatch.setattr("app.routers.playwright.is_live_view_available", lambda: True)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}",
            "Origin": "http://localhost:4747",
        }

        response = client.get("/api/v1/playwright/live-view", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["url"].startswith("http://localhost:4747/vnc/vnc.html")
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_playwright_live_view_uses_referer_when_origin_missing(monkeypatch):
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

    monkeypatch.setattr("app.routers.playwright.is_live_view_available", lambda: True)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}",
            "Referer": "http://localhost:4747/sentinel/sessions",
        }

        response = client.get("/api/v1/playwright/live-view", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["url"].startswith("http://localhost:4747/vnc/vnc.html")
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_playwright_reset_browser_endpoint(monkeypatch):
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

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
    monkeypatch.setattr("app.routers.playwright._resolve_browser_manager", lambda request: _StubManager())

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.post("/api/v1/playwright/reset-browser", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["reset"] is True
        assert payload["url"] == "about:blank"
        assert payload["stale_lock_cleared"] is True
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
