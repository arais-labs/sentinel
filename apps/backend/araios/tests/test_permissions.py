def test_agent_allowed_action(client, agent_headers):
    """Agent can list leads (allowed)."""
    resp = client.get("/api/leads", headers=agent_headers)
    assert resp.status_code == 200


def test_agent_approval_action(client, agent_headers, admin_headers):
    """Agent trying to delete a lead gets 202 (approval required)."""
    # First create a lead as admin
    lead = client.post(
        "/api/leads",
        json={"name": "Test Lead", "company": "TestCo"},
        headers=admin_headers,
    ).json()

    # Agent tries to delete → gets 202
    resp = client.delete(f"/api/leads/{lead['id']}", headers=agent_headers)
    assert resp.status_code == 202

    # Lead should still exist
    resp = client.get("/api/leads", headers=admin_headers)
    leads = resp.json()["leads"]
    assert any(l["id"] == lead["id"] for l in leads)


def test_agent_denied_action(client, agent_headers):
    """Agent trying to resolve approvals gets 403."""
    resp = client.post(
        "/api/approvals/fake-id/approve",
        headers=agent_headers,
    )
    assert resp.status_code == 403


def test_admin_bypasses_all_permissions(client, admin_headers):
    """Admin can delete leads directly."""
    lead = client.post(
        "/api/leads",
        json={"name": "Delete Me", "company": "Gone"},
        headers=admin_headers,
    ).json()

    resp = client.delete(f"/api/leads/{lead['id']}", headers=admin_headers)
    assert resp.status_code == 200


def test_agent_proposal_create_needs_approval(client, agent_headers):
    """Agent creating a proposal triggers approval."""
    resp = client.post(
        "/api/proposals",
        json={"leadName": "Test", "company": "Co", "proposalTitle": "Test Prop", "value": 5000},
        headers=agent_headers,
    )
    assert resp.status_code == 202
