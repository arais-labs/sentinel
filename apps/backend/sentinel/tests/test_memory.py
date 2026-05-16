import os

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.main import app
from tests.fake_db import FakeDB
from tests.helpers import install_fake_db_overrides, restore_test_app


MEMORY_API = "/api/v1/instances/main/memory"
ONBOARDING_COMPLETE_API = "/api/v1/instances/main/onboarding/complete"


def test_memory_store_search_stats_delete():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        token_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert token_resp.status_code == 200
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        create = client.post(
            MEMORY_API,
            json={
                "content": "Core project convention",
                "category": "core",
                "metadata": {"source": "unit-test"},
            },
            headers=headers,
        )
        assert create.status_code == 200
        memory_id = create.json()["id"]

        search = client.get(f"{MEMORY_API}?category=core", headers=headers)
        assert search.status_code == 200
        assert any(item["id"] == memory_id for item in search.json()["items"])

        stats = client.get(f"{MEMORY_API}/stats", headers=headers)
        assert stats.status_code == 200
        payload = stats.json()
        assert payload["total_memories"] >= 1
        assert payload["categories"].get("core", 0) >= 1

        delete = client.delete(f"{MEMORY_API}/{memory_id}", headers=headers)
        assert delete.status_code == 200
        assert delete.json()["status"] == "deleted"
    finally:
        restore_test_app(old_init)


def test_memory_hierarchy_endpoints():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        token_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert token_resp.status_code == 200
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        create_root = client.post(
            MEMORY_API,
            json={
                "content": "Root content",
                "title": "Root Node",
                "summary": "Root summary",
                "category": "project",
                "importance": 90,
                "pinned": True,
                "metadata": {"source": "unit-test"},
            },
            headers=headers,
        )
        assert create_root.status_code == 200
        root_id = create_root.json()["id"]

        create_child = client.post(
            MEMORY_API,
            json={
                "content": "Child content alpha",
                "title": "Child Node",
                "summary": "Child summary",
                "category": "project",
                "parent_id": root_id,
                "metadata": {},
            },
            headers=headers,
        )
        assert create_child.status_code == 200
        child_id = create_child.json()["id"]

        roots = client.get(f"{MEMORY_API}/roots", headers=headers)
        assert roots.status_code == 200
        assert any(item["id"] == root_id for item in roots.json()["items"])

        node = client.get(f"{MEMORY_API}/nodes/{root_id}", headers=headers)
        assert node.status_code == 200
        assert node.json()["title"] == "Root Node"

        children = client.get(f"{MEMORY_API}/nodes/{root_id}/children", headers=headers)
        assert children.status_code == 200
        assert any(item["id"] == child_id for item in children.json()["items"])

        update = client.patch(
            f"{MEMORY_API}/nodes/{child_id}",
            json={"title": "Child Node v2", "importance": 55},
            headers=headers,
        )
        assert update.status_code == 200
        assert update.json()["title"] == "Child Node v2"
        assert update.json()["importance"] == 55

        search = client.post(
            f"{MEMORY_API}/search",
            json={"query": "alpha", "root_id": root_id, "limit": 20},
            headers=headers,
        )
        assert search.status_code == 200
        assert any(item["id"] == child_id for item in search.json()["items"])

        touch = client.post(f"{MEMORY_API}/nodes/{child_id}/touch", headers=headers)
        assert touch.status_code == 200
        assert touch.json()["last_accessed_at"] is not None
    finally:
        restore_test_app(old_init)


def test_system_memories_are_backend_protected():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        token_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert token_resp.status_code == 200
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        complete = client.post(ONBOARDING_COMPLETE_API, json={}, headers=headers)
        assert complete.status_code == 200

        roots = client.get(f"{MEMORY_API}/roots?category=core", headers=headers)
        assert roots.status_code == 200
        items = roots.json()["items"]
        system = next(item for item in items if item["system_key"] == "agent_identity")
        system_id = system["id"]
        assert system["is_system"] is True
        assert system["pinned"] is True

        workspace_root = client.post(
            MEMORY_API,
            json={
                "content": "workspace root",
                "title": "Workspace",
                "category": "project",
            },
            headers=headers,
        )
        assert workspace_root.status_code == 200
        workspace_root_id = workspace_root.json()["id"]

        unpin = client.patch(
            f"{MEMORY_API}/nodes/{system_id}",
            json={"pinned": False},
            headers=headers,
        )
        assert unpin.status_code == 403

        move_under_workspace = client.patch(
            f"{MEMORY_API}/nodes/{system_id}",
            json={"parent_id": workspace_root_id},
            headers=headers,
        )
        assert move_under_workspace.status_code == 403

        recategorize = client.patch(
            f"{MEMORY_API}/nodes/{system_id}",
            json={"category": "preference"},
            headers=headers,
        )
        assert recategorize.status_code == 403

        delete = client.delete(f"{MEMORY_API}/{system_id}", headers=headers)
        assert delete.status_code == 403

        create_child = client.post(
            MEMORY_API,
            json={
                "content": "should fail",
                "category": "core",
                "parent_id": system_id,
            },
            headers=headers,
        )
        assert create_child.status_code == 403
    finally:
        restore_test_app(old_init)
