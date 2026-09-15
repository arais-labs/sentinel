from tests.test_workspaces import workspace_app as workspace_app
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services.runtime import workspace_containers as containers
from tests.test_workspace_removal import linked_workspace


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["running", "stopped"])
async def test_edit_resources_uses_same_workspace_and_preserves_stopped_state(
    workspace_app, monkeypatch, state
):
    client, _, _, *_ = workspace_app
    workspace_id, _ = await linked_workspace(workspace_app)
    monkeypatch.setattr(
        containers,
        "statuses",
        AsyncMock(
            return_value={
                str(workspace_id): {
                    "state": state,
                    "resources": {"cpus": 2, "memory_gib": 2, "disk_gib": 32},
                }
            }
        ),
    )
    allocation = {"cpus": 4, "memory_gib": 8, "disk_gib": 64}
    response = await client.patch(f"workspaces/{workspace_id}", json={"resources": allocation})
    assert response.status_code == 200, response.text
    assert response.json()["resources"] == allocation
    assert response.json()["container_state"] == ("stopped" if state == "stopped" else "preparing")
    assert containers.start.await_args.kwargs == {
        "resources": allocation,
        "notification_context": {"instanceName": "main", "name": "Removal test"},
    }
    containers.stop.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("active", ["parent", "child"])
async def test_resize_blocks_agents_in_parent_and_child_sessions(workspace_app, active):
    client, _, _, first, _, registry = workspace_app
    workspace_id, child = await linked_workspace(workspace_app)
    containers.start.reset_mock()
    run = await registry.start(str(first.id if active == "parent" else child.id), asyncio.sleep(60))
    try:
        response = await client.patch(
            f"workspaces/{workspace_id}",
            json={"resources": {"cpus": 4, "memory_gib": 8, "disk_gib": 64}},
        )
        assert response.status_code == 409, response.text
        containers.start.assert_not_awaited()
    finally:
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


@pytest.mark.asyncio
async def test_disk_shrink_is_rejected_before_changing_runtime(workspace_app, monkeypatch):
    client, *_ = workspace_app
    workspace_id, _ = await linked_workspace(workspace_app)
    monkeypatch.setattr(
        containers,
        "statuses",
        AsyncMock(
            return_value={
                str(workspace_id): {
                    "state": "running",
                    "resources": {"cpus": 4, "memory_gib": 8, "disk_gib": 64},
                }
            }
        ),
    )
    containers.start.reset_mock()
    response = await client.patch(
        f"workspaces/{workspace_id}",
        json={"resources": {"cpus": 2, "memory_gib": 2, "disk_gib": 32}},
    )
    assert response.status_code == 422, response.text
    containers.start.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["running", "stopped"])
async def test_change_folder_preserves_workspace(workspace_app, monkeypatch, tmp_path, state):
    client, _, _, *_ = workspace_app
    workspace_id, _ = await linked_workspace(workspace_app)
    monkeypatch.setattr(
        containers, "statuses", AsyncMock(return_value={str(workspace_id): {"state": state}})
    )
    folder = tmp_path / "new project"
    folder.mkdir()
    response = await client.patch(f"workspaces/{workspace_id}", json={"directory": str(folder)})
    assert response.status_code == 200, response.text
    assert response.json()["directory"] == str(folder)
    assert response.json()["id"] == str(workspace_id)
    assert response.json()["container_state"] == ("stopped" if state == "stopped" else "preparing")
    assert containers.start.await_args.args[1] == str(folder)
    containers.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_folder_rejects_missing_path(workspace_app, tmp_path):
    client, _, _, *_ = workspace_app
    workspace_id, _ = await linked_workspace(workspace_app)
    containers.start.reset_mock()
    response = await client.patch(
        f"workspaces/{workspace_id}", json={"directory": str(tmp_path / "missing")}
    )
    assert response.status_code == 422
    containers.start.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("active", ["parent", "child"])
async def test_folder_change_blocks_active_agents(workspace_app, tmp_path, active):
    client, _, _, first, _, registry = workspace_app
    workspace_id, child = await linked_workspace(workspace_app)
    containers.start.reset_mock()
    run = await registry.start(str(first.id if active == "parent" else child.id), asyncio.sleep(60))
    try:
        response = await client.patch(
            f"workspaces/{workspace_id}", json={"directory": str(tmp_path)}
        )
        assert response.status_code == 409, response.text
        containers.start.assert_not_awaited()
    finally:
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)
