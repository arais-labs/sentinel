from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.main import app
from tests.fake_db import FakeDB
from tests.helpers import install_fake_db_overrides, restore_test_app


def test_agent_modes_endpoint_returns_backend_defined_modes():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        response = client.get("/api/v1/instances/main/agent-modes", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["default_mode"] == "normal"
        ids = [item["id"] for item in payload["items"]]
        assert ids == ["normal", "full_permission", "read_only", "code_review"]
    finally:
        restore_test_app(old_init)
