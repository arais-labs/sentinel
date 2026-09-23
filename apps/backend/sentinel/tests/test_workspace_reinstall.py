from tests.test_workspaces import workspace_app as workspace_app
from unittest.mock import AsyncMock
import asyncio

import pytest

from app.models import Session, Workspace
from app.services.runtime import workspace_containers as containers
from tests.test_workspace_removal import linked_workspace


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [None, 6, 7])
async def test_remote_reinstall_uses_reviewed_revision_and_worker_defaults(
    workspace_app, monkeypatch, revision
):
    from app.models.manager import Machine
    from app.services.runtime import ssh_runtime, worker_catalog

    client, factory, machine, *_ = workspace_app
    workspace_id, _ = await linked_workspace(workspace_app)
    async with ssh_runtime.ManagerSessionLocal() as db:
        remote = await db.get(Machine, machine.id)
        remote.provider = "ssh"
        await db.commit()
    spec = {
        "name": "Worker name",
        "project": "/remote/current-project",
        "distribution": "debian",
        "desktop": "gnome",
        "browser": "firefox",
        "tools": ["git"],
        "resources": {"cpus": 4, "memory_gib": 8, "disk_gib": 64},
    }
    monkeypatch.setattr(
        worker_catalog,
        "snapshot",
        AsyncMock(return_value={"workspaces": {str(workspace_id): {"revision": 7, "spec": spec}}}),
    )
    reinstall = AsyncMock()
    monkeypatch.setattr(containers, "reinstall", reinstall)
    settings = {"name": "Reviewed name"}
    if revision is not None:
        settings["revision"] = revision
    response = await client.post(
        f"workspaces/{workspace_id}/reinstall",
        json={"confirmed": True, "settings": settings},
    )
    if revision != 7:
        assert response.status_code == 409, response.text
        reinstall.assert_not_awaited()
        return
    assert response.status_code == 200, response.text
    assert reinstall.await_args.kwargs["revision"] == 7
    assert reinstall.await_args.kwargs["spec"] == {**spec, "name": "Reviewed name"}
    assert reinstall.await_args.args == (workspace_id, spec["project"], spec["tools"])
    async with factory() as db:
        row = await db.get(Workspace, workspace_id)
        assert (row.directory, row.distribution, row.browser) == (
            spec["project"],
            spec["distribution"],
            spec["browser"],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, {}, {"confirmed": False}])
async def test_reinstall_requires_explicit_destructive_confirmation(
    workspace_app, monkeypatch, payload
):
    client, *_ = workspace_app
    workspace_id, _ = await linked_workspace(workspace_app)
    reinstall = AsyncMock()
    monkeypatch.setattr(containers, "reinstall", reinstall)
    response = await client.post(f"workspaces/{workspace_id}/reinstall", json=payload)
    assert response.status_code == 422
    reinstall.assert_not_awaited()


@pytest.mark.asyncio
async def test_reinstall_preserves_workspace_and_session_bindings(workspace_app, monkeypatch):
    client, factory, _, first, _, _ = workspace_app
    workspace_id, _ = await linked_workspace(workspace_app)
    reinstall = AsyncMock()
    monkeypatch.setattr(containers, "reinstall", reinstall)
    response = await client.post(f"workspaces/{workspace_id}/reinstall", json={"confirmed": True})
    assert response.status_code == 200, response.text
    assert response.json()["container_state"] == "preparing"
    async with factory() as db:
        workspace = await db.get(Workspace, workspace_id)
        reinstall.assert_awaited_once_with(
            workspace_id,
            workspace.directory,
            workspace.development_tools,
            notification_context={"instanceName": "main", "name": workspace.name},
        )
        assert (await db.get(Session, first.id)).workspace_id == workspace_id
    containers.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmed_os_change_reinstalls_and_updates_workspace(workspace_app, monkeypatch):
    client, factory, *_ = workspace_app
    workspace_id, _ = await linked_workspace(workspace_app)
    reinstall = AsyncMock()
    monkeypatch.setattr(containers, "reinstall", reinstall)
    response = await client.post(
        f"workspaces/{workspace_id}/reinstall",
        json={"confirmed": True, "settings": {"distribution": "ubuntu"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["distribution"] == "ubuntu"
    assert reinstall.await_args.kwargs["distribution"] == "ubuntu"
    async with factory() as db:
        assert (await db.get(Workspace, workspace_id)).distribution == "ubuntu"


@pytest.mark.asyncio
@pytest.mark.parametrize("active", ["parent", "child"])
async def test_reinstall_blocks_active_agents(workspace_app, monkeypatch, active):
    client, _, _, first, _, registry = workspace_app
    workspace_id, child = await linked_workspace(workspace_app)
    reinstall = AsyncMock()
    monkeypatch.setattr(containers, "reinstall", reinstall)
    run = await registry.start(str(first.id if active == "parent" else child.id), asyncio.sleep(60))
    try:
        response = await client.post(
            f"workspaces/{workspace_id}/reinstall", json={"confirmed": True}
        )
        assert response.status_code == 409, response.text
        reinstall.assert_not_awaited()
    finally:
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["preparing", "stopping"])
async def test_reinstall_blocks_busy_workspace(workspace_app, monkeypatch, state):
    client, *_ = workspace_app
    workspace_id, _ = await linked_workspace(workspace_app)
    reinstall = AsyncMock()
    monkeypatch.setattr(containers, "reinstall", reinstall)
    monkeypatch.setattr(
        containers,
        "statuses",
        AsyncMock(return_value={str(workspace_id): {"state": state}}),
    )
    response = await client.post(f"workspaces/{workspace_id}/reinstall", json={"confirmed": True})
    assert response.status_code == 409, response.text
    reinstall.assert_not_awaited()
