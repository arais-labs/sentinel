import os

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from tests.fake_db import FakeDB


def test_memory_store_search_stats_delete():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        token_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert token_resp.status_code == 200
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        create = client.post(
            "/api/v1/memory",
            json={
                "content": "Core project convention",
                "category": "core",
                "metadata": {"source": "unit-test"},
            },
            headers=headers,
        )
        assert create.status_code == 200
        memory_id = create.json()["id"]

        search = client.get("/api/v1/memory?category=core", headers=headers)
        assert search.status_code == 200
        assert any(item["id"] == memory_id for item in search.json()["items"])

        stats = client.get("/api/v1/memory/stats", headers=headers)
        assert stats.status_code == 200
        payload = stats.json()
        assert payload["total_memories"] >= 1
        assert payload["categories"].get("core", 0) >= 1

        delete = client.delete(f"/api/v1/memory/{memory_id}", headers=headers)
        assert delete.status_code == 200
        assert delete.json()["status"] == "deleted"
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_memory_hierarchy_endpoints():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        token_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert token_resp.status_code == 200
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        create_root = client.post(
            "/api/v1/memory",
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
            "/api/v1/memory",
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

        roots = client.get("/api/v1/memory/roots", headers=headers)
        assert roots.status_code == 200
        assert any(item["id"] == root_id for item in roots.json()["items"])

        node = client.get(f"/api/v1/memory/nodes/{root_id}", headers=headers)
        assert node.status_code == 200
        assert node.json()["title"] == "Root Node"

        children = client.get(f"/api/v1/memory/nodes/{root_id}/children", headers=headers)
        assert children.status_code == 200
        assert any(item["id"] == child_id for item in children.json()["items"])

        update = client.patch(
            f"/api/v1/memory/nodes/{child_id}",
            json={"title": "Child Node v2", "importance": 55},
            headers=headers,
        )
        assert update.status_code == 200
        assert update.json()["title"] == "Child Node v2"
        assert update.json()["importance"] == 55

        search = client.post(
            "/api/v1/memory/search",
            json={"query": "alpha", "root_id": root_id, "limit": 20},
            headers=headers,
        )
        assert search.status_code == 200
        assert any(item["id"] == child_id for item in search.json()["items"])

        touch = client.post(f"/api/v1/memory/nodes/{child_id}/touch", headers=headers)
        assert touch.status_code == 200
        assert touch.json()["last_accessed_at"] is not None
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_system_memories_are_backend_protected():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        token_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert token_resp.status_code == 200
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        complete = client.post("/api/v1/onboarding/complete", json={}, headers=headers)
        assert complete.status_code == 200

        roots = client.get("/api/v1/memory/roots?category=core", headers=headers)
        assert roots.status_code == 200
        items = roots.json()["items"]
        system = next(item for item in items if item["system_key"] == "agent_identity")
        system_id = system["id"]
        assert system["is_system"] is True
        assert system["pinned"] is True

        workspace_root = client.post(
            "/api/v1/memory",
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
            f"/api/v1/memory/nodes/{system_id}",
            json={"pinned": False},
            headers=headers,
        )
        assert unpin.status_code == 403

        move_under_workspace = client.patch(
            f"/api/v1/memory/nodes/{system_id}",
            json={"parent_id": workspace_root_id},
            headers=headers,
        )
        assert move_under_workspace.status_code == 403

        recategorize = client.patch(
            f"/api/v1/memory/nodes/{system_id}",
            json={"category": "preference"},
            headers=headers,
        )
        assert recategorize.status_code == 403

        delete = client.delete(f"/api/v1/memory/{system_id}", headers=headers)
        assert delete.status_code == 403

        create_child = client.post(
            "/api/v1/memory",
            json={
                "content": "should fail",
                "category": "core",
                "parent_id": system_id,
            },
            headers=headers,
        )
        assert create_child.status_code == 403
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
