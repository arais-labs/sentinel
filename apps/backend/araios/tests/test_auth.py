def test_no_token_returns_401(client):
    resp = client.get("/api/leads")
    assert resp.status_code == 401


def test_invalid_token_returns_401(client):
    resp = client.get("/api/leads", headers={"Authorization": "Bearer wrong-token"})
    assert resp.status_code == 401


def test_admin_token_passes(client, admin_headers):
    resp = client.get("/api/leads", headers=admin_headers)
    assert resp.status_code == 200


def test_agent_token_passes_on_allowed(client, agent_headers):
    resp = client.get("/api/leads", headers=agent_headers)
    assert resp.status_code == 200


def test_missing_bearer_prefix(client):
    resp = client.get("/api/leads", headers={"Authorization": "test-admin-token"})
    assert resp.status_code == 401
