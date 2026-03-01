def test_crud_github_tasks(client, admin_headers):
    resp = client.post(
        "/api/github-tasks",
        json={"client": "domu", "repo": "Domu-ai/monorepo", "type": "pr_review", "title": "Fix bug"},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    task = resp.json()

    resp = client.get("/api/github-tasks", headers=admin_headers)
    assert len(resp.json()["tasks"]) == 1

    # Filter by client
    resp = client.get("/api/github-tasks?client=domu", headers=admin_headers)
    assert len(resp.json()["tasks"]) == 1

    resp = client.get("/api/github-tasks?client=other", headers=admin_headers)
    assert len(resp.json()["tasks"]) == 0

    # Update with deep merge on workPackage
    resp = client.patch(
        f"/api/github-tasks/{task['id']}",
        json={"workPackage": {"review": "Looks good"}},
        headers=admin_headers,
    )
    assert resp.json()["workPackage"]["review"] == "Looks good"
