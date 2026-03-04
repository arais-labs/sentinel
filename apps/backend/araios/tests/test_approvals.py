def _create_task(client, headers, title: str) -> dict:
    resp = client.post("/api/tasks", json={"title": title}, headers=headers)
    assert resp.status_code == 201
    return resp.json()


def _request_delete_approval(client, agent_headers, task_id: str) -> str:
    resp = client.delete(f"/api/tasks/{task_id}", headers=agent_headers)
    assert resp.status_code == 202
    return resp.json()["detail"]["approval"]["id"]


def test_approval_flow_approve_executes_delete(client, admin_headers, agent_headers):
    task = _create_task(client, admin_headers, "delete-after-approve")
    approval_id = _request_delete_approval(client, agent_headers, task["id"])

    approve = client.post(f"/api/approvals/{approval_id}/approve", headers=admin_headers)
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"

    tasks = client.get("/api/tasks", headers=admin_headers).json()["tasks"]
    assert not any(item["id"] == task["id"] for item in tasks)


def test_approval_flow_reject_keeps_task(client, admin_headers, agent_headers):
    task = _create_task(client, admin_headers, "keep-after-reject")
    approval_id = _request_delete_approval(client, agent_headers, task["id"])

    reject = client.post(f"/api/approvals/{approval_id}/reject", headers=admin_headers)
    assert reject.status_code == 200
    assert reject.json()["status"] == "rejected"

    tasks = client.get("/api/tasks", headers=admin_headers).json()["tasks"]
    assert any(item["id"] == task["id"] for item in tasks)


def test_cannot_approve_twice(client, admin_headers, agent_headers):
    task = _create_task(client, admin_headers, "double-approve-guard")
    approval_id = _request_delete_approval(client, agent_headers, task["id"])

    first = client.post(f"/api/approvals/{approval_id}/approve", headers=admin_headers)
    assert first.status_code == 200

    second = client.post(f"/api/approvals/{approval_id}/approve", headers=admin_headers)
    assert second.status_code == 400
    assert "already approved" in second.json()["detail"]
