"""
Comprehensive tests for the DB-backed permissions + approval execution flow.

Covers:
1. Permission CRUD (admin-only list, update)
2. Dynamic permission toggling (allow ↔ approval ↔ deny)
3. Approval execution for every action verb (create, update, delete)
4. Deep-merge preservation on approved updates (pricing, icp, work_package)
5. Positioning single-row approval
6. Agent can read permissions
"""

from app.database.models import Permission


# ─── Helper ───

def seed_permission(client, admin_headers, action, level):
    """Set a specific permission level via PATCH."""
    resp = client.patch(
        f"/api/permissions/{action}",
        json={"level": level},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    return resp.json()


def create_approval_and_approve(client, agent_headers, admin_headers, method, url, json=None):
    """Agent triggers an action → get 202 → admin approves → return approval response."""
    func = getattr(client, method)
    kwargs = {"headers": agent_headers}
    if json is not None:
        kwargs["json"] = json
    resp = func(url, **kwargs)
    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    approval_id = resp.json()["detail"]["approval"]["id"]

    resp = client.post(f"/api/approvals/{approval_id}/approve", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    return resp.json()


# ═══════════════════════════════════════════════════════════════
# 1. PERMISSION ROUTER
# ═══════════════════════════════════════════════════════════════

def test_admin_can_list_permissions(client, admin_headers):
    resp = client.get("/api/permissions", headers=admin_headers)
    assert resp.status_code == 200
    perms = resp.json()["permissions"]
    assert len(perms) > 0
    actions = [p["action"] for p in perms]
    assert "leads.list" in actions
    assert "leads.delete" in actions


def test_agent_can_list_permissions(client, agent_headers):
    resp = client.get("/api/permissions", headers=agent_headers)
    assert resp.status_code == 200
    assert len(resp.json()["permissions"]) > 0


def test_agent_cannot_update_permissions(client, agent_headers):
    resp = client.patch(
        "/api/permissions/leads.delete",
        json={"level": "allow"},
        headers=agent_headers,
    )
    assert resp.status_code == 403


def test_admin_can_update_permission(client, admin_headers):
    resp = seed_permission(client, admin_headers, "leads.delete", "deny")
    assert resp["level"] == "deny"

    # Verify it persists
    resp = client.get("/api/permissions", headers=admin_headers)
    perm = next(p for p in resp.json()["permissions"] if p["action"] == "leads.delete")
    assert perm["level"] == "deny"


def test_update_permission_invalid_level(client, admin_headers):
    resp = client.patch(
        "/api/permissions/leads.delete",
        json={"level": "superadmin"},
        headers=admin_headers,
    )
    assert resp.status_code == 400


def test_update_permission_not_found(client, admin_headers):
    resp = client.patch(
        "/api/permissions/nonexistent.action",
        json={"level": "allow"},
        headers=admin_headers,
    )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════
# 2. DYNAMIC PERMISSION TOGGLING
# ═══════════════════════════════════════════════════════════════

def test_toggle_allow_to_approval(client, admin_headers, agent_headers):
    """Switch leads.list from allow → approval, agent should get 202."""
    # Default is allow
    resp = client.get("/api/leads", headers=agent_headers)
    assert resp.status_code == 200

    # Toggle to approval
    seed_permission(client, admin_headers, "leads.list", "approval")
    resp = client.get("/api/leads", headers=agent_headers)
    assert resp.status_code == 202

    # Toggle back to allow
    seed_permission(client, admin_headers, "leads.list", "allow")
    resp = client.get("/api/leads", headers=agent_headers)
    assert resp.status_code == 200


def test_toggle_approval_to_deny(client, admin_headers, agent_headers):
    """Switch leads.delete from approval → deny, agent should get 403."""
    # Default is approval
    lead = client.post(
        "/api/leads",
        json={"name": "Test", "company": "Co"},
        headers=admin_headers,
    ).json()

    resp = client.delete(f"/api/leads/{lead['id']}", headers=agent_headers)
    assert resp.status_code == 202

    # Toggle to deny
    seed_permission(client, admin_headers, "leads.delete", "deny")
    resp = client.delete(f"/api/leads/{lead['id']}", headers=agent_headers)
    assert resp.status_code == 403


def test_toggle_approval_to_allow(client, admin_headers, agent_headers):
    """Switch leads.delete from approval → allow, agent can delete directly."""
    lead = client.post(
        "/api/leads",
        json={"name": "Direct Delete", "company": "Co"},
        headers=admin_headers,
    ).json()

    # Toggle to allow
    seed_permission(client, admin_headers, "leads.delete", "allow")
    resp = client.delete(f"/api/leads/{lead['id']}", headers=agent_headers)
    assert resp.status_code == 200

    # Verify deleted
    resp = client.get("/api/leads", headers=admin_headers)
    assert not any(l["id"] == lead["id"] for l in resp.json()["leads"])


# ═══════════════════════════════════════════════════════════════
# 3. APPROVAL EXECUTION — DELETE
# ═══════════════════════════════════════════════════════════════

def test_approval_execute_delete_lead(client, admin_headers, agent_headers):
    lead = client.post(
        "/api/leads",
        json={"name": "To Delete", "company": "Gone"},
        headers=admin_headers,
    ).json()

    create_approval_and_approve(client, agent_headers, admin_headers, "delete", f"/api/leads/{lead['id']}")

    resp = client.get("/api/leads", headers=admin_headers)
    assert not any(l["id"] == lead["id"] for l in resp.json()["leads"])


def test_approval_execute_delete_competitor(client, admin_headers, agent_headers):
    comp = client.post(
        "/api/competitors",
        json={"name": "Rival", "website": "https://rival.com"},
        headers=admin_headers,
    ).json()

    create_approval_and_approve(client, agent_headers, admin_headers, "delete", f"/api/competitors/{comp['id']}")

    resp = client.get("/api/competitors", headers=admin_headers)
    assert not any(c["id"] == comp["id"] for c in resp.json()["competitors"])


def test_approval_execute_delete_client(client, admin_headers, agent_headers):
    cl = client.post(
        "/api/clients",
        json={"name": "Client X", "company": "Corp"},
        headers=admin_headers,
    ).json()

    create_approval_and_approve(client, agent_headers, admin_headers, "delete", f"/api/clients/{cl['id']}")

    resp = client.get("/api/clients", headers=admin_headers)
    assert not any(c["id"] == cl["id"] for c in resp.json()["clients"])


# ═══════════════════════════════════════════════════════════════
# 4. APPROVAL EXECUTION — CREATE
# ═══════════════════════════════════════════════════════════════

def test_approval_execute_create_proposal(client, admin_headers, agent_headers):
    """proposals.create defaults to approval — verify it executes on approve."""
    create_approval_and_approve(
        client, agent_headers, admin_headers,
        "post", "/api/proposals",
        json={"leadName": "Alice", "company": "ACME", "proposalTitle": "Big Deal", "value": 50000},
    )

    resp = client.get("/api/proposals", headers=admin_headers)
    proposals = resp.json()["proposals"]
    assert len(proposals) == 1
    assert proposals[0]["leadName"] == "Alice"
    assert proposals[0]["value"] == 50000


# ═══════════════════════════════════════════════════════════════
# 5. APPROVAL EXECUTION — UPDATE
# ═══════════════════════════════════════════════════════════════

def test_approval_execute_update_proposal(client, admin_headers, agent_headers):
    """proposals.update defaults to approval — verify approved update applies."""
    prop = client.post(
        "/api/proposals",
        json={"leadName": "Bob", "company": "Inc", "proposalTitle": "Initial", "value": 1000},
        headers=admin_headers,
    ).json()

    create_approval_and_approve(
        client, agent_headers, admin_headers,
        "patch", f"/api/proposals/{prop['id']}",
        json={"value": 9999, "status": "sent"},
    )

    resp = client.get("/api/proposals", headers=admin_headers)
    updated = next(p for p in resp.json()["proposals"] if p["id"] == prop["id"])
    assert updated["value"] == 9999
    assert updated["status"] == "sent"
    # Untouched fields preserved
    assert updated["leadName"] == "Bob"


# ═══════════════════════════════════════════════════════════════
# 6. DEEP MERGE ON APPROVAL
# ═══════════════════════════════════════════════════════════════

def test_approval_deep_merge_competitor_pricing(client, admin_headers, agent_headers):
    """When competitors.update approval executes, pricing should be deep-merged."""
    # Toggle competitors.update to approval
    seed_permission(client, admin_headers, "competitors.update", "approval")

    comp = client.post(
        "/api/competitors",
        json={"name": "MergeCo", "pricing": {"starter": "$10", "pro": "$50"}},
        headers=admin_headers,
    ).json()

    create_approval_and_approve(
        client, agent_headers, admin_headers,
        "patch", f"/api/competitors/{comp['id']}",
        json={"pricing": {"enterprise": "$200"}},
    )

    resp = client.get("/api/competitors", headers=admin_headers)
    updated = next(c for c in resp.json()["competitors"] if c["id"] == comp["id"])
    # All three keys should exist
    assert updated["pricing"]["starter"] == "$10"
    assert updated["pricing"]["pro"] == "$50"
    assert updated["pricing"]["enterprise"] == "$200"


def test_approval_deep_merge_positioning_icp(client, admin_headers, agent_headers):
    """When positioning.update approval executes, icp should be deep-merged."""
    # Seed positioning
    client.patch(
        "/api/positioning",
        json={"icp": {"primary": "Engineers"}, "tagline": "Build fast"},
        headers=admin_headers,
    )

    # Toggle positioning.update to approval
    seed_permission(client, admin_headers, "positioning.update", "approval")

    create_approval_and_approve(
        client, agent_headers, admin_headers,
        "patch", "/api/positioning",
        json={"icp": {"secondary": "Architects"}},
    )

    resp = client.get("/api/positioning", headers=admin_headers)
    icp = resp.json()["icp"]
    assert icp["primary"] == "Engineers"
    assert icp["secondary"] == "Architects"


# ═══════════════════════════════════════════════════════════════
# 7. POSITIONING SINGLE-ROW APPROVAL
# ═══════════════════════════════════════════════════════════════

def test_positioning_update_approval_works(client, admin_headers, agent_headers):
    """Positioning is a single-row model — approval execution should find id='default'."""
    # Create the positioning row first
    client.patch(
        "/api/positioning",
        json={"tagline": "Original"},
        headers=admin_headers,
    )

    # Toggle to approval
    seed_permission(client, admin_headers, "positioning.update", "approval")

    create_approval_and_approve(
        client, agent_headers, admin_headers,
        "patch", "/api/positioning",
        json={"tagline": "Updated via approval"},
    )

    resp = client.get("/api/positioning", headers=admin_headers)
    assert resp.json()["tagline"] == "Updated via approval"


# ═══════════════════════════════════════════════════════════════
# 8. REJECT FLOW — ACTION NOT EXECUTED
# ═══════════════════════════════════════════════════════════════

def test_rejected_delete_not_executed(client, admin_headers, agent_headers):
    lead = client.post(
        "/api/leads",
        json={"name": "Safe Lead"},
        headers=admin_headers,
    ).json()

    resp = client.delete(f"/api/leads/{lead['id']}", headers=agent_headers)
    assert resp.status_code == 202
    approval_id = resp.json()["detail"]["approval"]["id"]

    # Reject
    resp = client.post(f"/api/approvals/{approval_id}/reject", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"

    # Lead still exists
    resp = client.get("/api/leads", headers=admin_headers)
    assert any(l["id"] == lead["id"] for l in resp.json()["leads"])


def test_rejected_create_not_executed(client, admin_headers, agent_headers):
    resp = client.post(
        "/api/proposals",
        json={"leadName": "Nope", "proposalTitle": "Rejected"},
        headers=agent_headers,
    )
    assert resp.status_code == 202
    approval_id = resp.json()["detail"]["approval"]["id"]

    # Reject
    client.post(f"/api/approvals/{approval_id}/reject", headers=admin_headers)

    # Proposal not created
    resp = client.get("/api/proposals", headers=admin_headers)
    assert len(resp.json()["proposals"]) == 0


# ═══════════════════════════════════════════════════════════════
# 9. ADMIN BYPASSES PERMISSIONS REGARDLESS OF LEVEL
# ═══════════════════════════════════════════════════════════════

def test_admin_bypasses_deny(client, admin_headers):
    """Even if a permission is set to deny, admin can still execute."""
    seed_permission(client, admin_headers, "leads.create", "deny")

    resp = client.post(
        "/api/leads",
        json={"name": "Admin Override"},
        headers=admin_headers,
    )
    assert resp.status_code == 201


def test_admin_bypasses_approval(client, admin_headers):
    """Admin doesn't trigger approval flow — action executes directly."""
    lead = client.post(
        "/api/leads",
        json={"name": "Direct Admin Delete"},
        headers=admin_headers,
    ).json()

    # leads.delete is "approval" by default but admin bypasses
    resp = client.delete(f"/api/leads/{lead['id']}", headers=admin_headers)
    assert resp.status_code == 200
