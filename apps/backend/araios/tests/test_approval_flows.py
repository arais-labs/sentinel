def _set_permission(client, admin_headers, action: str, level: str):
    resp = client.patch(
        f"/api/permissions/{action}",
        json={"level": level},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json() == {"action": action, "level": level}


def _create_task(client, admin_headers, title: str) -> dict:
    resp = client.post("/api/tasks", json={"title": title}, headers=admin_headers)
    assert resp.status_code == 201
    return resp.json()


def test_permissions_list_contains_tasks_actions(client, admin_headers):
    resp = client.get("/api/permissions", headers=admin_headers)
    assert resp.status_code == 200
    actions = [row["action"] for row in resp.json()["permissions"]]
    assert "tasks.list" in actions
    assert "tasks.create" in actions
    assert "tasks.update" in actions
    assert "tasks.delete" in actions


def test_agent_cannot_update_permissions(client, agent_headers):
    resp = client.patch(
        "/api/permissions/tasks.delete",
        json={"level": "allow"},
        headers=agent_headers,
    )
    assert resp.status_code == 403


def test_toggle_tasks_delete_allow_approval_deny(client, admin_headers, agent_headers):
    # deny -> forbidden
    _set_permission(client, admin_headers, "tasks.delete", "deny")
    deny_task = _create_task(client, admin_headers, "deny-delete")
    denied = client.delete(f"/api/tasks/{deny_task['id']}", headers=agent_headers)
    assert denied.status_code == 403

    # approval -> creates approval record
    _set_permission(client, admin_headers, "tasks.delete", "approval")
    approval_task = _create_task(client, admin_headers, "approval-delete")
    pending = client.delete(f"/api/tasks/{approval_task['id']}", headers=agent_headers)
    assert pending.status_code == 202
    approval_id = pending.json()["detail"]["approval"]["id"]
    approve = client.post(f"/api/approvals/{approval_id}/approve", headers=admin_headers)
    assert approve.status_code == 200

    # allow -> direct delete
    _set_permission(client, admin_headers, "tasks.delete", "allow")
    allow_task = _create_task(client, admin_headers, "allow-delete")
    direct = client.delete(f"/api/tasks/{allow_task['id']}", headers=agent_headers)
    assert direct.status_code == 200
    assert direct.json() == {"ok": True}

    # restore default
    _set_permission(client, admin_headers, "tasks.delete", "approval")
