def test_crud_tasks_as_admin(client, admin_headers):
    resp = client.post(
        "/api/tasks",
        json={"client": "exampleco", "repo": "Exampleco/monorepo", "type": "pr_review", "title": "Fix bug"},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    task = resp.json()

    resp = client.get("/api/tasks", headers=admin_headers)
    assert len(resp.json()["tasks"]) == 1

    # Filter by client
    resp = client.get("/api/tasks?client=exampleco", headers=admin_headers)
    assert len(resp.json()["tasks"]) == 1

    resp = client.get("/api/tasks?client=other", headers=admin_headers)
    assert len(resp.json()["tasks"]) == 0

    # Update with deep merge on workPackage
    resp = client.patch(
        f"/api/tasks/{task['id']}",
        json={"workPackage": {"review": "Looks good"}},
        headers=admin_headers,
    )
    assert resp.json()["workPackage"]["review"] == "Looks good"


def test_agent_can_create_and_update_task(client, agent_headers):
    create_resp = client.post(
        "/api/tasks",
        json={"title": "Agent Task", "status": "in_analysis", "owner": "agent"},
        headers=agent_headers,
    )
    assert create_resp.status_code == 201
    task = create_resp.json()
    assert task["updatedBy"] == "agent"

    update_resp = client.patch(
        f"/api/tasks/{task['id']}",
        json={"status": "work_ready", "handoffTo": "admin"},
        headers=agent_headers,
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["status"] == "work_ready"
    assert updated["handoffTo"] == "admin"
    assert updated["updatedBy"] == "agent"


def test_agent_delete_task_requires_approval(client, admin_headers, agent_headers):
    task = client.post(
        "/api/tasks",
        json={"title": "Delete Me"},
        headers=admin_headers,
    ).json()

    delete_resp = client.delete(f"/api/tasks/{task['id']}", headers=agent_headers)
    assert delete_resp.status_code == 202
    approval_id = delete_resp.json()["detail"]["approval"]["id"]

    approve_resp = client.post(f"/api/approvals/{approval_id}/approve", headers=admin_headers)
    assert approve_resp.status_code == 200

    all_tasks = client.get("/api/tasks", headers=admin_headers).json()["tasks"]
    assert not any(item["id"] == task["id"] for item in all_tasks)
