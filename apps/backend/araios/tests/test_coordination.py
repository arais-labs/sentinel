"""Tests for the agent coordination module."""


def test_esprit_can_post_message(client, esprit_headers):
    resp = client.post(
        "/api/coordination",
        json={"message": "Hello from Esprit", "context": {"telegram_msg_id": 42}},
        headers=esprit_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["agent"] == "esprit"
    assert data["message"] == "Hello from Esprit"
    assert data["context"]["telegram_msg_id"] == 42


def test_ronnor_can_post_message(client, ronnor_headers):
    resp = client.post(
        "/api/coordination",
        json={"message": "Hello from RonNor"},
        headers=ronnor_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["agent"] == "ronnor"


def test_admin_can_post_message(client, admin_headers):
    resp = client.post(
        "/api/coordination",
        json={"message": "Admin note"},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["agent"] == "admin"


def test_get_messages_returns_chat_order(client, esprit_headers, ronnor_headers):
    """Messages should be returned oldest-first (chat order)."""
    client.post("/api/coordination", json={"message": "First"}, headers=esprit_headers)
    client.post("/api/coordination", json={"message": "Second"}, headers=ronnor_headers)
    client.post("/api/coordination", json={"message": "Third"}, headers=esprit_headers)

    resp = client.get("/api/coordination", headers=esprit_headers)
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert len(msgs) == 3
    assert msgs[0]["message"] == "First"
    assert msgs[0]["agent"] == "esprit"
    assert msgs[1]["message"] == "Second"
    assert msgs[1]["agent"] == "ronnor"
    assert msgs[2]["message"] == "Third"


def test_get_messages_limit(client, esprit_headers):
    for i in range(5):
        client.post("/api/coordination", json={"message": f"Msg {i}"}, headers=esprit_headers)

    resp = client.get("/api/coordination?limit=3", headers=esprit_headers)
    msgs = resp.json()["messages"]
    assert len(msgs) == 3
    # Should be the 3 most recent, oldest first
    assert msgs[0]["message"] == "Msg 2"
    assert msgs[2]["message"] == "Msg 4"


def test_both_agents_see_all_messages(client, esprit_headers, ronnor_headers):
    """Both agents can read the full log — no inbox isolation."""
    client.post("/api/coordination", json={"message": "Esprit msg"}, headers=esprit_headers)
    client.post("/api/coordination", json={"message": "RonNor msg"}, headers=ronnor_headers)

    # Esprit reads
    resp = client.get("/api/coordination", headers=esprit_headers)
    assert len(resp.json()["messages"]) == 2

    # RonNor reads the same
    resp = client.get("/api/coordination", headers=ronnor_headers)
    assert len(resp.json()["messages"]) == 2


def test_legacy_agent_token_still_works(client, agent_headers):
    """The original AGENT_TOKEN should still authenticate."""
    resp = client.post(
        "/api/coordination",
        json={"message": "Legacy agent"},
        headers=agent_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["agent"] == "agent"


def test_unauthenticated_rejected(client):
    resp = client.post("/api/coordination", json={"message": "No auth"})
    assert resp.status_code == 401

    resp = client.get("/api/coordination")
    assert resp.status_code == 401


def test_context_is_optional(client, esprit_headers):
    resp = client.post(
        "/api/coordination",
        json={"message": "No context"},
        headers=esprit_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["context"] is None
