from tests.test_workspaces import workspace_app as workspace_app
from unittest.mock import AsyncMock
import asyncio

import pytest

from app.models import Session, Workspace
from app.services.runtime import workspace_containers as containers
from tests.test_workspace_removal import linked_workspace


@pytest.mark.asyncio
async def test_reinstall_preserves_workspace_and_session_bindings(workspace_app, monkeypatch):
    client, factory, _, first, _, _ = workspace_app
    workspace_id, _ = await linked_workspace(workspace_app)
    reinstall = AsyncMock()
    monkeypatch.setattr(containers, "reinstall", reinstall)
    response = await client.post(f"workspaces/{workspace_id}/reinstall")
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
@pytest.mark.parametrize("active", ["parent", "child"])
async def test_reinstall_blocks_active_agents(workspace_app, monkeypatch, active):
    client, _, _, first, _, registry = workspace_app
    workspace_id, child = await linked_workspace(workspace_app)
    reinstall = AsyncMock()
    monkeypatch.setattr(containers, "reinstall", reinstall)
    run = await registry.start(str(first.id if active == "parent" else child.id), asyncio.sleep(60))
    try:
        response = await client.post(f"workspaces/{workspace_id}/reinstall")
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
    response = await client.post(f"workspaces/{workspace_id}/reinstall")
    assert response.status_code == 409, response.text
    reinstall.assert_not_awaited()
