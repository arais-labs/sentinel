from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_manager_db
from app.middleware.auth import TokenPayload, require_admin
from app.routers import instances as instances_router
from app.services.instances import InstanceRegistryService
from tests.fake_db import FakeDB


class _TestRegistryService(InstanceRegistryService):
    def __init__(self):
        super().__init__()
        self.created: list[str] = []
        self.initialized: list[str] = []
        self.dropped: list[str] = []

    async def _database_exists(self, database_name: str) -> bool:
        return database_name in self.created

    async def _create_database(self, database_name: str) -> None:
        self.created.append(database_name)

    async def _init_database(self, database_name: str) -> None:
        self.initialized.append(database_name)

    async def _drop_database(self, database_name: str) -> None:
        self.dropped.append(database_name)


def _client() -> tuple[TestClient, FakeDB, _TestRegistryService]:
    app = FastAPI()
    app.include_router(instances_router.router, prefix="/api/v1/instances")
    fake_db = FakeDB()
    service = _TestRegistryService()

    async def _manager_db():
        yield fake_db

    async def _admin():
        return TokenPayload(
            sub="admin",
            role="admin",
            agent_id=None,
            exp=9999999999,
            iat=1,
            jti="test",
            token_type="access",
        )

    app.dependency_overrides[get_manager_db] = _manager_db
    app.dependency_overrides[require_admin] = _admin
    app.dependency_overrides[instances_router._service] = lambda: service
    return TestClient(app), fake_db, service


def test_instances_api_create_and_list():
    client, _db, service = _client()

    create_response = client.post("/api/v1/instances", json={"name": "Main"})
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["name"] == "main"
    assert created["database_name"].startswith("sentinel_main_")
    assert "runtime_backend" not in created
    assert "runtime_config" not in created
    assert service.created == [created["database_name"]]

    list_response = client.get("/api/v1/instances")
    assert list_response.status_code == 200
    assert [row["name"] for row in list_response.json()] == ["main"]
    assert "runtime_backend" not in list_response.json()[0]
    assert "runtime_config" not in list_response.json()[0]


def test_instances_api_rejects_legacy_runtime_fields():
    client, _db, _service = _client()

    create_response = client.post(
        "/api/v1/instances",
        json={"name": "Main", "runtime_backend": "qemu", "runtime_config": {"x": True}},
    )

    assert create_response.status_code == 422


def test_instances_api_duplicate_name_conflicts():
    client, _db, _service = _client()

    assert client.post("/api/v1/instances", json={"name": "main"}).status_code == 201
    duplicate_response = client.post("/api/v1/instances", json={"name": "main"})

    assert duplicate_response.status_code == 409


def test_instances_api_rename_and_delete():
    client, _db, service = _client()
    created = client.post("/api/v1/instances", json={"name": "main"}).json()

    rename_response = client.post("/api/v1/instances/main/rename", json={"name": "Client A"})
    assert rename_response.status_code == 200
    renamed = rename_response.json()
    assert renamed["name"] == "client-a"
    assert renamed["database_name"] == created["database_name"]

    delete_response = client.delete("/api/v1/instances/client-a")
    assert delete_response.status_code == 204
    assert service.dropped == [created["database_name"]]
    assert client.get("/api/v1/instances/client-a").status_code == 404


def test_instances_api_removes_normalized_runtime_context_names(monkeypatch):
    client, _db, _service = _client()
    client.post("/api/v1/instances", json={"name": "main"})
    client.app.state.instance_stop_event = object()
    removed: list[str] = []

    async def _remove(name: str) -> None:
        removed.append(name)

    async def _get_or_create(**_kwargs):
        return None

    monkeypatch.setattr(instances_router.instance_runtime_context_registry, "remove", _remove)
    monkeypatch.setattr(instances_router.instance_runtime_context_registry, "get_or_create", _get_or_create)

    rename_response = client.post("/api/v1/instances/Main/rename", json={"name": "Client A"})
    assert rename_response.status_code == 200
    delete_response = client.delete("/api/v1/instances/Client%20A")
    assert delete_response.status_code == 204

    assert removed == ["main", "client-a"]
