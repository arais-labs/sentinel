def test_approval_flow_approve(client, admin_headers, agent_headers):
    """Full flow: agent triggers approval → admin approves → write executed."""
    # Agent tries to delete a lead → gets approval
    lead = client.post(
        "/api/leads",
        json={"name": "Protected Lead", "company": "SafeCo"},
        headers=admin_headers,
    ).json()

    resp = client.delete(f"/api/leads/{lead['id']}", headers=agent_headers)
    assert resp.status_code == 202
    approval_id = resp.json()["detail"]["approval"]["id"]

    # Check approval is pending
    resp = client.get("/api/approvals?status=pending", headers=admin_headers)
    approvals = resp.json()["approvals"]
    assert any(a["id"] == approval_id for a in approvals)

    # Admin approves
    resp = client.post(f"/api/approvals/{approval_id}/approve", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"

    # Lead should now be deleted
    resp = client.get("/api/leads", headers=admin_headers)
    leads = resp.json()["leads"]
    assert not any(l["id"] == lead["id"] for l in leads)


def test_approval_flow_reject(client, admin_headers, agent_headers):
    """Admin rejects → no write executed."""
    lead = client.post(
        "/api/leads",
        json={"name": "Keep Me", "company": "StayCo"},
        headers=admin_headers,
    ).json()

    resp = client.delete(f"/api/leads/{lead['id']}", headers=agent_headers)
    assert resp.status_code == 202
    approval_id = resp.json()["detail"]["approval"]["id"]

    # Admin rejects
    resp = client.post(f"/api/approvals/{approval_id}/reject", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"

    # Lead should still exist
    resp = client.get("/api/leads", headers=admin_headers)
    leads = resp.json()["leads"]
    assert any(l["id"] == lead["id"] for l in leads)


def test_cannot_approve_twice(client, admin_headers, agent_headers):
    """Cannot approve an already-approved approval."""
    lead = client.post(
        "/api/leads",
        json={"name": "Double", "company": "Twice"},
        headers=admin_headers,
    ).json()

    resp = client.delete(f"/api/leads/{lead['id']}", headers=agent_headers)
    approval_id = resp.json()["detail"]["approval"]["id"]

    client.post(f"/api/approvals/{approval_id}/approve", headers=admin_headers)
    resp = client.post(f"/api/approvals/{approval_id}/approve", headers=admin_headers)
    assert resp.status_code == 400


def test_create_approval_manually(client, agent_headers, admin_headers):
    """Agent can create an approval directly via POST."""
    resp = client.post(
        "/api/approvals",
        json={
            "action": "slack.send",
            "resource": "slack",
            "description": "Send message to #general",
            "payload": {"channel": "#general", "message": "Hello"},
        },
        headers=agent_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"
