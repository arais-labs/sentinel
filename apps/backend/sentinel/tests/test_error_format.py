import uuid

from fastapi.testclient import TestClient


from app.main import app
from tests.fake_db import FakeDB
from tests.helpers import install_fake_db_overrides, restore_test_app


def test_error_response_format_consistency():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )

        headers = {"x-sentinel-desktop-token": "test-desktop-transport-token"}

        not_found = client.get(f"/api/v1/instances/main/sessions/{uuid.uuid4()}", headers=headers)
        assert not_found.status_code == 404
        assert not_found.json()["error"]["code"] == "not_found"

        session = client.post(
            "/api/v1/instances/main/sessions", json={"title": "err-test"}, headers=headers
        )
        assert session.status_code == 200
        session_id = session.json()["id"]

        validation = client.post(
            f"/api/v1/instances/main/sessions/{session_id}/messages",
            json={"role": "user", "content": "   ", "metadata": {}},
            headers=headers,
        )
        assert validation.status_code == 422
        assert validation.json()["error"]["code"] == "validation_error"

    finally:
        restore_test_app(old_init)
