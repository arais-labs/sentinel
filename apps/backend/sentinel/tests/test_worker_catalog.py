from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.engine import create_database_engine
from app.models import Base, Workspace
from app.routers.workspaces import discover_worker_workspaces
from app.services.runtime import worker_catalog


@pytest.mark.asyncio
async def test_new_clients_discover_identical_worker_ids_and_refresh_cache(tmp_path, monkeypatch):
    machine_id, workspace_id = uuid4(), uuid4()
    record = {
        "revision": 1,
        "spec": {
            "name": "Worker project",
            "project": "/remote/project",
            "distribution": "ubuntu",
            "tools": ["git", "desktop"],
            "resources": {"cpus": 4, "memory_gib": 8, "disk_gib": 64},
        },
    }
    snapshot = {
        "worker_id": str(uuid4()),
        "workspaces": {str(workspace_id): record},
        "states": {str(workspace_id): {"state": "running"}},
    }
    monkeypatch.setattr(worker_catalog, "snapshot", AsyncMock(return_value=snapshot))
    for client in ("first", "second"):
        engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path}/{client}.sqlite")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            manager = AsyncMock()
            manager.get.return_value = SimpleNamespace(id=machine_id, provider="ssh")
            async with factory() as db:
                rows = await discover_worker_workspaces(machine_id, db, manager)
                assert [(row.id, row.name, row.revision) for row in rows] == [
                    (workspace_id, record["spec"]["name"], record["revision"])
                ]
                assert rows[0].container_state == "running"
                row = await db.get(Workspace, workspace_id)
                row.directory = "/stale-client-copy"
                await db.commit()
                rows = await discover_worker_workspaces(machine_id, db, manager)
                assert rows[0].directory == "/remote/project"
                assert (await db.get(Workspace, workspace_id)).directory == "/remote/project"
                # A deleted workspace may retain conversation references while
                # the worker reuses its name for a new UUID.
                replacement = uuid4()
                snapshot["workspaces"] = {str(replacement): record}
                snapshot["states"] = {str(replacement): {"state": "stopped"}}
                rows = await discover_worker_workspaces(machine_id, db, manager)
                assert [row.id for row in rows] == [replacement]
                assert await db.get(Workspace, workspace_id) is not None
                snapshot["workspaces"] = {str(workspace_id): record}
                snapshot["states"] = {str(workspace_id): {"state": "running"}}
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_unreachable_worker_does_not_delete_cached_references(tmp_path, monkeypatch):
    from fastapi import HTTPException

    machine_id, workspace_id = uuid4(), uuid4()
    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path}/client.sqlite")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            db.add(
                Workspace(
                    id=workspace_id,
                    machine_id=machine_id,
                    name="Cached",
                    directory="/remote/project",
                )
            )
            await db.commit()
            manager = AsyncMock()
            manager.get.return_value = SimpleNamespace(provider="ssh")
            monkeypatch.setattr(
                worker_catalog, "snapshot", AsyncMock(side_effect=ConnectionError("Disconnected"))
            )
            with pytest.raises(HTTPException) as error:
                await discover_worker_workspaces(machine_id, db, manager)
            assert error.value.status_code == 503
            assert await db.get(Workspace, workspace_id) is not None
    finally:
        await engine.dispose()
