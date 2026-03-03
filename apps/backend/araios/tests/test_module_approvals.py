def _pending_approvals(client, admin_headers):
    resp = client.get("/api/approvals?status=pending", headers=admin_headers)
    assert resp.status_code == 200
    return resp.json()["approvals"]


def _tool_action(action_id: str, *, label: str) -> dict:
    return {
        "id": action_id,
        "label": label,
        "description": f"{label} action",
        "params": [],
        "code": "result = {'ok': True}",
    }


def test_invalid_module_create_fails_without_approval(client, admin_headers, agent_headers):
    before_ids = {a["id"] for a in _pending_approvals(client, admin_headers)}

    resp = client.post(
        "/api/modules",
        json={"actions": []},
        headers=agent_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Module name is required"

    after_ids = {a["id"] for a in _pending_approvals(client, admin_headers)}
    assert after_ids == before_ids


def test_module_update_uses_update_approval_and_executes(client, admin_headers, agent_headers):
    create_resp = client.post(
        "/api/modules",
        json={"name": "slack_patch_test", "label": "Slack Patch Test"},
        headers=admin_headers,
    )
    assert create_resp.status_code == 201

    update_resp = client.patch(
        "/api/modules/slack_patch_test",
        json={"label": "Slack Patch Test v2"},
        headers=agent_headers,
    )
    assert update_resp.status_code == 202
    approval_id = update_resp.json()["detail"]["approval"]["id"]

    approval = next(a for a in _pending_approvals(client, admin_headers) if a["id"] == approval_id)
    assert approval["action"] == "modules.update"
    assert approval["resource"] == "modules"
    assert approval["resource_id"] == "slack_patch_test"

    approve_resp = client.post(f"/api/approvals/{approval_id}/approve", headers=admin_headers)
    assert approve_resp.status_code == 200

    module_resp = client.get("/api/modules/slack_patch_test", headers=admin_headers)
    assert module_resp.status_code == 200
    assert module_resp.json()["label"] == "Slack Patch Test v2"


def test_invalid_module_update_fails_without_approval(client, admin_headers, agent_headers):
    create_resp = client.post(
        "/api/modules",
        json={"name": "empty_patch_module", "label": "Empty Patch Module"},
        headers=admin_headers,
    )
    assert create_resp.status_code == 201

    before_ids = {a["id"] for a in _pending_approvals(client, admin_headers)}

    update_resp = client.patch(
        "/api/modules/empty_patch_module",
        json={},
        headers=agent_headers,
    )
    assert update_resp.status_code == 400
    assert "At least one editable module field is required" in update_resp.json()["detail"]

    after_ids = {a["id"] for a in _pending_approvals(client, admin_headers)}
    assert after_ids == before_ids


def test_module_delete_uses_delete_approval_and_executes(client, admin_headers, agent_headers):
    create_resp = client.post(
        "/api/modules",
        json={"name": "delete_module_test", "label": "Delete Module Test"},
        headers=admin_headers,
    )
    assert create_resp.status_code == 201

    delete_resp = client.delete("/api/modules/delete_module_test", headers=agent_headers)
    assert delete_resp.status_code == 202
    approval_id = delete_resp.json()["detail"]["approval"]["id"]

    approval = next(a for a in _pending_approvals(client, admin_headers) if a["id"] == approval_id)
    assert approval["action"] == "modules.delete"
    assert approval["resource"] == "modules"
    assert approval["resource_id"] == "delete_module_test"

    approve_resp = client.post(f"/api/approvals/{approval_id}/approve", headers=admin_headers)
    assert approve_resp.status_code == 200

    module_resp = client.get("/api/modules/delete_module_test", headers=admin_headers)
    assert module_resp.status_code == 404


def test_module_update_actions_patch_preserves_unmentioned_actions(
    client,
    admin_headers,
    agent_headers,
):
    create_resp = client.post(
        "/api/modules",
        json={
            "name": "slack_action_merge_test",
            "label": "Slack Action Merge Test",
            "type": "tool",
            "actions": [
                _tool_action("send_message", label="Send Message"),
                _tool_action("history", label="History"),
                _tool_action("unread", label="Unread"),
            ],
        },
        headers=admin_headers,
    )
    assert create_resp.status_code == 201

    update_resp = client.patch(
        "/api/modules/slack_action_merge_test",
        json={
            "actions": [
                _tool_action("history", label="History v2"),
            ]
        },
        headers=agent_headers,
    )
    assert update_resp.status_code == 202
    approval_id = update_resp.json()["detail"]["approval"]["id"]

    approve_resp = client.post(f"/api/approvals/{approval_id}/approve", headers=admin_headers)
    assert approve_resp.status_code == 200

    module_resp = client.get("/api/modules/slack_action_merge_test", headers=admin_headers)
    assert module_resp.status_code == 200
    actions = module_resp.json()["actions"]
    assert [a["id"] for a in actions] == ["send_message", "history", "unread"]
    history = next(a for a in actions if a["id"] == "history")
    assert history["label"] == "History v2"


def test_module_update_invalid_action_payload_fails_without_approval(
    client,
    admin_headers,
    agent_headers,
):
    create_resp = client.post(
        "/api/modules",
        json={"name": "invalid_action_patch_test", "label": "Invalid Action Patch Test"},
        headers=admin_headers,
    )
    assert create_resp.status_code == 201

    before_ids = {a["id"] for a in _pending_approvals(client, admin_headers)}

    update_resp = client.patch(
        "/api/modules/invalid_action_patch_test",
        json={"actions": [{"label": "Missing ID"}]},
        headers=agent_headers,
    )
    assert update_resp.status_code == 400
    assert update_resp.json()["detail"] == "Each action in 'actions' requires a non-empty 'id'"

    after_ids = {a["id"] for a in _pending_approvals(client, admin_headers)}
    assert after_ids == before_ids
