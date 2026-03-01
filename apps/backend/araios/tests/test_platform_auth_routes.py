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
