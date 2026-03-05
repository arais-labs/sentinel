def test_no_token_returns_401(client):
    resp = client.get("/api/tasks")
    assert resp.status_code == 401


def test_invalid_token_returns_401(client):
    resp = client.get("/api/tasks", headers={"Authorization": "Bearer wrong-token"})
    assert resp.status_code == 401


def test_missing_bearer_prefix_returns_401(client):
    resp = client.get("/api/tasks", headers={"Authorization": "test-admin-token"})
    assert resp.status_code == 401


def test_admin_token_returns_200(client, admin_headers):
    resp = client.get("/api/tasks", headers=admin_headers)
    assert resp.status_code == 200
    assert "tasks" in resp.json()


def test_agent_token_returns_200(client, agent_headers):
    resp = client.get("/api/tasks", headers=agent_headers)
    assert resp.status_code == 200
    assert "tasks" in resp.json()
