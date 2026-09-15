from __future__ import annotations


from fastapi.testclient import TestClient


from app.main import app
from tests.fake_db import FakeDB
from tests.helpers import install_fake_db_overrides, restore_test_app


def test_agent_modes_endpoint_returns_backend_defined_modes():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )
        headers = {"x-sentinel-desktop-token": "test-desktop-transport-token"}

        response = client.get("/api/v1/agent-modes", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["default_mode"] == "normal"
        ids = [item["id"] for item in payload["items"]]
        assert ids == [
            "normal",
            "full_permission",
            "read_only",
            "code_review",
            "interactive_output",
        ]
    finally:
        restore_test_app(old_init)
