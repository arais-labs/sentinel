import os

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from tests.fake_db import FakeDB


def _auth_headers(client: TestClient) -> dict[str, str]:
    token_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert token_resp.status_code == 200
    token = token_resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_memory_backup_export_and_import_roundtrip():
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
        headers = _auth_headers(client)

        complete = client.post("/api/v1/onboarding/complete", json={}, headers=headers)
        assert complete.status_code == 200

        root = client.post(
            "/api/v1/memory",
            json={
                "content": "Project backup root",
                "title": "Backup Root",
                "category": "project",
                "pinned": False,
            },
            headers=headers,
        )
        assert root.status_code == 200
        root_id = root.json()["id"]

        child = client.post(
            "/api/v1/memory",
            json={
                "content": "Project backup child",
                "title": "Backup Child",
                "category": "project",
                "parent_id": root_id,
            },
            headers=headers,
        )
        assert child.status_code == 200

        exported = client.get("/api/v1/memory/backup/export", headers=headers)
        assert exported.status_code == 200
        document = exported.json()
        assert document["schema_version"] == "memory_backup_v1"
        assert isinstance(document["nodes"], list)
        assert any(item.get("is_system") is True for item in document["nodes"])
        assert any(item.get("title") == "Backup Root" for item in document["nodes"])

        delete_root = client.delete(f"/api/v1/memory/{root_id}", headers=headers)
        assert delete_root.status_code == 200

        imported = client.post(
            "/api/v1/memory/backup/import",
            json={
                "document": document,
                "mode": "merge",
            },
            headers=headers,
        )
        assert imported.status_code == 200
        payload = imported.json()
        assert payload["total_in_backup"] == len(document["nodes"])
        assert payload["created"] >= 1

        project_nodes = client.get("/api/v1/memory?category=project&limit=200", headers=headers)
        assert project_nodes.status_code == 200
        assert any(item["title"] == "Backup Root" for item in project_nodes.json()["items"])
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_memory_backup_import_rejects_invalid_parent_reference():
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
        headers = _auth_headers(client)

        imported = client.post(
            "/api/v1/memory/backup/import",
            json={
                "document": {
                    "schema_version": "memory_backup_v1",
                    "exported_at": "2026-03-06T00:00:00Z",
                    "nodes": [
                        {
                            "external_id": "node-1",
                            "parent_external_id": "missing-parent",
                            "content": "x",
                            "category": "project",
                            "importance": 1,
                            "pinned": False,
                            "is_system": False,
                            "metadata": {},
                        }
                    ],
                }
            },
            headers=headers,
        )
        assert imported.status_code == 400
        body = imported.json()
        message = str((body.get("error") or {}).get("message") or "")
        assert "unknown parent_external_id" in message
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_memory_backup_import_rejects_system_node_without_valid_key():
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
        headers = _auth_headers(client)

        imported = client.post(
            "/api/v1/memory/backup/import",
            json={
                "document": {
                    "schema_version": "memory_backup_v1",
                    "exported_at": "2026-03-06T00:00:00Z",
                    "nodes": [
                        {
                            "external_id": "sys-1",
                            "parent_external_id": None,
                            "content": "system",
                            "title": "Unknown System Title",
                            "category": "core",
                            "importance": 100,
                            "pinned": True,
                            "is_system": True,
                            "metadata": {},
                        }
                    ],
                }
            },
            headers=headers,
        )
        assert imported.status_code == 400
        body = imported.json()
        message = str((body.get("error") or {}).get("message") or "")
        assert "valid system_key" in message
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_memory_backup_import_rejects_duplicate_external_ids():
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
        headers = _auth_headers(client)

        imported = client.post(
            "/api/v1/memory/backup/import",
            json={
                "document": {
                    "schema_version": "memory_backup_v1",
                    "exported_at": "2026-03-06T00:00:00Z",
                    "nodes": [
                        {
                            "external_id": "dup-1",
                            "content": "one",
                            "category": "project",
                            "importance": 1,
                            "pinned": False,
                            "is_system": False,
                            "metadata": {},
                        },
                        {
                            "external_id": "dup-1",
                            "content": "two",
                            "category": "project",
                            "importance": 2,
                            "pinned": False,
                            "is_system": False,
                            "metadata": {},
                        },
                    ],
                }
            },
            headers=headers,
        )
        assert imported.status_code == 400
        body = imported.json()
        message = str((body.get("error") or {}).get("message") or "")
        assert "Duplicate external_id" in message
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_memory_backup_import_rejects_non_system_node_with_system_key():
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
        headers = _auth_headers(client)

        imported = client.post(
            "/api/v1/memory/backup/import",
            json={
                "document": {
                    "schema_version": "memory_backup_v1",
                    "exported_at": "2026-03-06T00:00:00Z",
                    "nodes": [
                        {
                            "external_id": "node-1",
                            "content": "x",
                            "category": "project",
                            "importance": 1,
                            "pinned": False,
                            "is_system": False,
                            "system_key": "agent_identity",
                            "metadata": {},
                        }
                    ],
                }
            },
            headers=headers,
        )
        assert imported.status_code == 400
        body = imported.json()
        message = str((body.get("error") or {}).get("message") or "")
        assert "cannot define system_key" in message
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_memory_backup_replace_non_system_preserves_system_roots():
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
        headers = _auth_headers(client)

        complete = client.post("/api/v1/onboarding/complete", json={}, headers=headers)
        assert complete.status_code == 200

        project = client.post(
            "/api/v1/memory",
            json={
                "content": "temporary project node",
                "title": "Temp Project",
                "category": "project",
            },
            headers=headers,
        )
        assert project.status_code == 200

        imported = client.post(
            "/api/v1/memory/backup/import",
            json={
                "mode": "replace_non_system",
                "document": {
                    "schema_version": "memory_backup_v1",
                    "exported_at": "2026-03-06T00:00:00Z",
                    "nodes": [],
                },
            },
            headers=headers,
        )
        assert imported.status_code == 200
        payload = imported.json()
        assert payload["deleted"] >= 1

        roots = client.get("/api/v1/memory/roots?category=core", headers=headers)
        assert roots.status_code == 200
        items = roots.json()["items"]
        system_keys = {item.get("system_key") for item in items if item.get("is_system") is True}
        assert "agent_identity" in system_keys
        assert "user_profile" in system_keys
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_memory_backup_merge_keeps_existing_legacy_system_title_unchanged():
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
        headers = _auth_headers(client)

        legacy = client.post(
            "/api/v1/memory",
            json={
                "content": "Legacy non-system identity",
                "title": "Agent Identity",
                "category": "core",
                "pinned": True,
                "importance": 100,
            },
            headers=headers,
        )
        assert legacy.status_code == 200
        legacy_id = legacy.json()["id"]

        imported = client.post(
            "/api/v1/memory/backup/import",
            json={
                "mode": "merge",
                "document": {
                    "schema_version": "memory_backup_v1",
                    "exported_at": "2026-03-06T00:00:00Z",
                    "nodes": [
                        {
                            "external_id": "sys-agent-identity",
                            "content": "System identity from backup",
                            "title": "Agent Identity",
                            "category": "core",
                            "importance": 100,
                            "pinned": True,
                            "is_system": True,
                            "system_key": "agent_identity",
                            "metadata": {},
                        }
                    ],
                },
            },
            headers=headers,
        )
        assert imported.status_code == 200
        payload = imported.json()
        assert payload["skipped"] == 1

        roots = client.get("/api/v1/memory/roots?category=core&limit=200", headers=headers)
        assert roots.status_code == 200
        items = roots.json()["items"]
        legacy_after = next(item for item in items if item["id"] == legacy_id)
        assert legacy_after["is_system"] is False
        system_items = [item for item in items if item.get("system_key") == "agent_identity"]
        assert len(system_items) == 0
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_memory_backup_replace_all_restores_system_memories_from_backup():
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
        headers = _auth_headers(client)

        legacy = client.post(
            "/api/v1/memory",
            json={
                "content": "Legacy identity memory",
                "title": "Agent Identity",
                "category": "core",
                "pinned": True,
                "importance": 100,
            },
            headers=headers,
        )
        assert legacy.status_code == 200
        legacy_id = legacy.json()["id"]

        imported = client.post(
            "/api/v1/memory/backup/import",
            json={
                "mode": "replace_all",
                "document": {
                    "schema_version": "memory_backup_v1",
                    "exported_at": "2026-03-06T00:00:00Z",
                    "nodes": [
                        {
                            "external_id": "sys-agent-identity",
                            "content": "System identity from backup",
                            "title": "Agent Identity",
                            "category": "core",
                            "importance": 100,
                            "pinned": True,
                            "is_system": True,
                            "system_key": "agent_identity",
                            "metadata": {},
                        }
                    ],
                },
            },
            headers=headers,
        )
        assert imported.status_code == 200
        payload = imported.json()
        assert payload["created"] >= 1
        assert payload["deleted"] >= 1

        roots = client.get("/api/v1/memory/roots?category=core", headers=headers)
        assert roots.status_code == 200
        items = roots.json()["items"]
        assert all(item["id"] != legacy_id for item in items)
        system_items = [item for item in items if item.get("system_key") == "agent_identity"]
        assert len(system_items) == 1
        assert system_items[0]["is_system"] is True
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_memory_backup_import_rolls_back_partial_writes_on_failure():
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
        headers = _auth_headers(client)

        complete = client.post("/api/v1/onboarding/complete", json={}, headers=headers)
        assert complete.status_code == 200

        imported = client.post(
            "/api/v1/memory/backup/import",
            json={
                "mode": "merge",
                "document": {
                    "schema_version": "memory_backup_v1",
                    "exported_at": "2026-03-06T00:00:00Z",
                    "nodes": [
                        {
                            "external_id": "project-root",
                            "content": "project root",
                            "title": "Project Root",
                            "category": "project",
                            "importance": 50,
                            "pinned": False,
                            "is_system": False,
                            "metadata": {},
                        },
                        {
                            "external_id": "agent-system",
                            "content": "system identity (kept existing)",
                            "title": "Agent Identity",
                            "category": "core",
                            "importance": 100,
                            "pinned": True,
                            "is_system": True,
                            "system_key": "agent_identity",
                            "metadata": {},
                        },
                        {
                            "external_id": "invalid-child",
                            "parent_external_id": "agent-system",
                            "content": "must fail",
                            "title": "Invalid Child",
                            "category": "project",
                            "importance": 10,
                            "pinned": False,
                            "is_system": False,
                            "metadata": {},
                        },
                    ],
                },
            },
            headers=headers,
        )
        assert imported.status_code == 403

        project_nodes = client.get("/api/v1/memory?category=project&limit=200", headers=headers)
        assert project_nodes.status_code == 200
        assert all(item.get("title") != "Project Root" for item in project_nodes.json()["items"])
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
