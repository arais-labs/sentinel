"""Tests for the shared document store."""


def test_create_document(client, admin_headers):
    resp = client.post(
        "/api/documents",
        json={"slug": "test-doc", "title": "Test Document", "content": "# Hello\n\nWorld"},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "test-doc"
    assert data["title"] == "Test Document"
    assert data["content"] == "# Hello\n\nWorld"
    assert data["author"] == "admin"
    assert data["version"] == 1


def test_create_duplicate_slug_returns_409(client, admin_headers):
    client.post(
        "/api/documents",
        json={"slug": "dup-slug", "title": "First"},
        headers=admin_headers,
    )
    resp = client.post(
        "/api/documents",
        json={"slug": "dup-slug", "title": "Second"},
        headers=admin_headers,
    )
    assert resp.status_code == 409


def test_list_documents_excludes_content(client, admin_headers):
    client.post(
        "/api/documents",
        json={"slug": "list-doc", "title": "Listed", "content": "big content"},
        headers=admin_headers,
    )
    resp = client.get("/api/documents", headers=admin_headers)
    assert resp.status_code == 200
    docs = resp.json()["documents"]
    assert len(docs) >= 1
    doc = next(d for d in docs if d["slug"] == "list-doc")
    assert "content" not in doc
    assert doc["title"] == "Listed"


def test_get_document_by_slug(client, admin_headers):
    client.post(
        "/api/documents",
        json={"slug": "get-me", "title": "Get Me", "content": "details here"},
        headers=admin_headers,
    )
    resp = client.get("/api/documents/get-me", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "get-me"
    assert data["content"] == "details here"


def test_get_nonexistent_returns_404(client, admin_headers):
    resp = client.get("/api/documents/does-not-exist", headers=admin_headers)
    assert resp.status_code == 404


def test_update_document(client, admin_headers):
    client.post(
        "/api/documents",
        json={"slug": "update-me", "title": "Original", "content": "v1"},
        headers=admin_headers,
    )
    resp = client.put(
        "/api/documents/update-me",
        json={"content": "v2 content", "title": "Updated Title"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "v2 content"
    assert data["title"] == "Updated Title"
    assert data["version"] == 2


def test_update_increments_version(client, admin_headers):
    client.post(
        "/api/documents",
        json={"slug": "versioned", "title": "V", "content": "1"},
        headers=admin_headers,
    )
    client.put("/api/documents/versioned", json={"content": "2"}, headers=admin_headers)
    resp = client.put("/api/documents/versioned", json={"content": "3"}, headers=admin_headers)
    assert resp.json()["version"] == 3


def test_optimistic_locking_success(client, admin_headers):
    client.post(
        "/api/documents",
        json={"slug": "locked", "title": "Lock", "content": "init"},
        headers=admin_headers,
    )
    resp = client.put(
        "/api/documents/locked",
        json={"content": "updated"},
        headers={**admin_headers, "If-Match": "1"},
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 2


def test_optimistic_locking_conflict(client, admin_headers):
    client.post(
        "/api/documents",
        json={"slug": "conflict", "title": "C", "content": "init"},
        headers=admin_headers,
    )
    # Update once (version goes to 2)
    client.put("/api/documents/conflict", json={"content": "v2"}, headers=admin_headers)
    # Try to update with stale version 1
    resp = client.put(
        "/api/documents/conflict",
        json={"content": "stale"},
        headers={**admin_headers, "If-Match": "1"},
    )
    assert resp.status_code == 409


def test_delete_document(client, admin_headers):
    client.post(
        "/api/documents",
        json={"slug": "delete-me", "title": "Delete", "content": "bye"},
        headers=admin_headers,
    )
    resp = client.delete("/api/documents/delete-me", headers=admin_headers)
    assert resp.status_code == 204

    resp = client.get("/api/documents/delete-me", headers=admin_headers)
    assert resp.status_code == 404


def test_delete_nonexistent_returns_404(client, admin_headers):
    resp = client.delete("/api/documents/nope", headers=admin_headers)
    assert resp.status_code == 404


def test_tags_filter(client, admin_headers):
    client.post(
        "/api/documents",
        json={"slug": "tagged-a", "title": "A", "content": "", "tags": ["infra", "ops"]},
        headers=admin_headers,
    )
    client.post(
        "/api/documents",
        json={"slug": "tagged-b", "title": "B", "content": "", "tags": ["dev"]},
        headers=admin_headers,
    )
    resp = client.get("/api/documents?tag=infra", headers=admin_headers)
    docs = resp.json()["documents"]
    assert len(docs) == 1
    assert docs[0]["slug"] == "tagged-a"


def test_agent_create_needs_approval(client, agent_headers):
    resp = client.post(
        "/api/documents",
        json={"slug": "agent-doc", "title": "Agent Doc", "content": "test"},
        headers=agent_headers,
    )
    assert resp.status_code == 202


def test_agent_update_needs_approval(client, admin_headers, agent_headers):
    # Admin creates doc first
    client.post(
        "/api/documents",
        json={"slug": "agent-edit", "title": "Edit Me", "content": "original"},
        headers=admin_headers,
    )
    resp = client.put(
        "/api/documents/agent-edit",
        json={"content": "modified"},
        headers=agent_headers,
    )
    assert resp.status_code == 202


def test_agent_delete_needs_approval(client, admin_headers, agent_headers):
    client.post(
        "/api/documents",
        json={"slug": "agent-del", "title": "Del Me", "content": "x"},
        headers=admin_headers,
    )
    resp = client.delete("/api/documents/agent-del", headers=agent_headers)
    assert resp.status_code == 202


def test_agent_can_list_documents(client, admin_headers, agent_headers):
    client.post(
        "/api/documents",
        json={"slug": "readable", "title": "Read", "content": "ok"},
        headers=admin_headers,
    )
    resp = client.get("/api/documents", headers=agent_headers)
    assert resp.status_code == 200
    assert len(resp.json()["documents"]) >= 1


def test_agent_can_read_document(client, admin_headers, agent_headers):
    client.post(
        "/api/documents",
        json={"slug": "agent-read", "title": "R", "content": "readable"},
        headers=admin_headers,
    )
    resp = client.get("/api/documents/agent-read", headers=agent_headers)
    assert resp.status_code == 200
    assert resp.json()["content"] == "readable"
