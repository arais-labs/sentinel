from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from tests.fake_db import FakeDB


def test_agent_modes_endpoint_returns_backend_defined_modes():
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
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        response = client.get("/api/v1/agent-modes", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["default_mode"] == "normal"
        ids = [item["id"] for item in payload["items"]]
        assert ids == ["normal", "full_permission", "read_only", "code_review"]
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
