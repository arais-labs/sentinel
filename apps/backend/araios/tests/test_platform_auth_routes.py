from app.database.models import PlatformApiKey, SystemSetting
from app.database.database import SessionLocal
from app.platform_auth import hash_api_key


def _admin_login(client, password: str = "admin") -> dict:
    response = client.post(
        "/platform/auth/login",
        json={"username": "admin", "password": password},
    )
    assert response.status_code == 200
    return response.json()


def test_login_and_change_password_flow(client):
    login = _admin_login(client)
    access_token = login["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    change = client.post(
        "/platform/auth/change-password",
        json={"current_password": "admin", "new_password": "new-admin-pass"},
        headers=headers,
    )
    assert change.status_code == 200
    assert change.json() == {"success": True}

    old_login = client.post(
        "/platform/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/platform/auth/login",
        json={"username": "admin", "password": "new-admin-pass"},
    )
    assert new_login.status_code == 200


def test_agent_key_create_exchange_and_revoke(client):
    login = _admin_login(client)
    access_token = login["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    created = client.post(
        "/platform/auth/agents",
        json={"label": "Runner", "agent_id": "runner-1", "subject": "runner-1"},
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    api_key = body["api_key"]
    agent_id = body["agent"]["id"]

    exchange = client.post("/platform/auth/token", json={"api_key": api_key})
    assert exchange.status_code == 200

    deleted = client.delete(f"/platform/auth/agents/{agent_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"success": True}

    exchange_after_revoke = client.post("/platform/auth/token", json={"api_key": api_key})
    assert exchange_after_revoke.status_code == 401


def test_admin_api_key_is_rejected_for_token_exchange(client):
    db = SessionLocal()
    try:
        db.add(
            PlatformApiKey(
                id="admin-key",
                label="Primary Admin",
                role="admin",
                subject="admin",
                agent_id="admin",
                key_hash=hash_api_key("sk-arais-admin-test"),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    exchange = client.post("/platform/auth/token", json={"api_key": "sk-arais-admin-test"})
    assert exchange.status_code == 401


def test_agent_key_update_and_rotate(client):
    login = _admin_login(client)
    access_token = login["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    created = client.post(
        "/platform/auth/agents",
        json={"label": "Runner", "agent_id": "runner-1", "subject": "runner-1"},
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    agent_row = body["agent"]
    old_api_key = body["api_key"]

    updated = client.patch(
        f"/platform/auth/agents/{agent_row['id']}",
        json={"label": "Runner v2", "subject": "runner-bot", "agent_id": "runner-2"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["label"] == "Runner v2"
    assert updated.json()["subject"] == "runner-bot"
    assert updated.json()["agent_id"] == "runner-2"

    rotate = client.post(
        f"/platform/auth/agents/{agent_row['id']}/rotate",
        headers=headers,
    )
    assert rotate.status_code == 200
    rotated = rotate.json()
    assert rotated["agent"]["id"] == agent_row["id"]
    assert rotated["api_key"] != old_api_key

    old_exchange = client.post("/platform/auth/token", json={"api_key": old_api_key})
    assert old_exchange.status_code == 401
    new_exchange = client.post("/platform/auth/token", json={"api_key": rotated["api_key"]})
    assert new_exchange.status_code == 200


def test_agent_key_update_conflict(client):
    login = _admin_login(client)
    access_token = login["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    first = client.post(
        "/platform/auth/agents",
        json={"label": "A", "agent_id": "a-1", "subject": "a-1"},
        headers=headers,
    )
    second = client.post(
        "/platform/auth/agents",
        json={"label": "B", "agent_id": "b-1", "subject": "b-1"},
        headers=headers,
    )
    assert first.status_code == 201
    assert second.status_code == 201

    conflict = client.patch(
        f"/platform/auth/agents/{second.json()['agent']['id']}",
        json={"agent_id": "a-1"},
        headers=headers,
    )
    assert conflict.status_code == 409


def test_app_links_returns_db_values_for_authenticated_user(client):
    db = SessionLocal()
    try:
        for key, value in (
            ("sentinel_frontend_url", "http://localhost:4747/sentinel"),
            ("araios_frontend_url", "http://localhost:4747/araios"),
        ):
            row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
            if row is None:
                db.add(SystemSetting(key=key, value=value))
            else:
                row.value = value
        db.commit()
    finally:
        db.close()

    login = _admin_login(client)
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    response = client.get("/platform/auth/app-links", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "sentinel_frontend_url": "http://localhost:4747/sentinel",
        "araios_frontend_url": "http://localhost:4747/araios",
    }
