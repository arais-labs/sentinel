from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.models import Session, SubAgentTask, Workspace
from app.services.runtime import workspace_containers as containers
from app.services.sessions.agent_run_registry import AgentRunRegistry
from tests import test_workspaces

workspace_app = test_workspaces.workspace_app


async def linked_workspace(fixture):
    client, factory, machine, first, second, _ = fixture
    response = await client.post(
        "workspaces",
        json={
            "name": "Removal test",
            "machine_id": str(machine.id),
            "directory": "/projects/keep",
        },
    )
    assert response.status_code == 201
    workspace_id = UUID(response.json()["id"])
    async with factory() as db:
        for parent in (first, second):
            row = await db.get(Session, parent.id)
            row.workspace_id = workspace_id
        child = Session(user_id="local", parent_session_id=first.id, title="Child agent")
        db.add(child)
        await db.commit()
    return workspace_id, child


@pytest.mark.asyncio
async def test_remove_detaches_all_sessions_and_broadcasts(workspace_app, monkeypatch):
    client, factory, _, first, second, _ = workspace_app
    workspace_id, child = await linked_workspace(workspace_app)
    from app.routers import workspaces
    from app.services.modules import runtime_services

    invalidate = AsyncMock()
    pool = type("Pool", (), {"remove": AsyncMock()})()
    monkeypatch.setattr(workspaces, "invalidate_runtime_for_session", invalidate)
    monkeypatch.setattr(runtime_services, "get_browser_pool", lambda: pool)
    manager = type("Manager", (), {"broadcast": AsyncMock()})()
    client._transport.app.state.ws_manager = manager
    preview = await client.get(f"workspaces/{workspace_id}/removal")
    assert preview.status_code == 200
    assert {s["id"] for s in preview.json()["sessions"]} == {
        str(s.id) for s in (first, second, child)
    }
    assert not any(s["running"] for s in preview.json()["sessions"])
    assert (await client.delete(f"workspaces/{workspace_id}")).status_code == 409
    containers.stop.assert_not_awaited()
    response = await client.delete(f"workspaces/{workspace_id}?detach_sessions=true")
    assert response.status_code == 204, response.text
    containers.stop.assert_awaited_once_with(workspace_id, delete=True)
    async with factory() as db:
        assert await db.get(Workspace, workspace_id) is None
        for original in (first, second, child):
            session = await db.get(Session, original.id)
            assert session is not None and session.workspace_id is None
            invalidate.assert_any_await("main", original.id, stop_remote=False)
            manager.broadcast.assert_any_await(
                str(original.id),
                {
                    "type": "workspace_changed",
                    "session_id": str(original.id),
                    "workspace_id": None,
                },
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("active", ["parent", "child", "delegated", "pending"])
async def test_remove_blocks_any_active_agent(workspace_app, active):
    client, factory, _, first, _, registry = workspace_app
    workspace_id, child = await linked_workspace(workspace_app)
    run = None
    if active in {"parent", "child"}:
        run = await registry.start(
            str(first.id if active == "parent" else child.id), asyncio.sleep(60)
        )
    else:
        async with factory() as db:
            db.add(
                SubAgentTask(
                    session_id=child.id,
                    objective="Working",
                    status="running" if active == "delegated" else "pending",
                )
            )
            await db.commit()
    try:
        preview = await client.get(f"workspaces/{workspace_id}/removal")
        assert any(s["running"] for s in preview.json()["sessions"])
        response = await client.delete(f"workspaces/{workspace_id}?detach_sessions=true")
        assert response.status_code == 409, response.text
        containers.stop.assert_not_awaited()
        async with factory() as db:
            assert (await db.get(Session, first.id)).workspace_id == workspace_id
            assert await db.get(Workspace, workspace_id)
    finally:
        if run:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_cleanup_keeps_bindings_and_can_retry(workspace_app):
    client, factory, _, first, _, _ = workspace_app
    workspace_id, _ = await linked_workspace(workspace_app)
    containers.stop.side_effect = containers.WorkspaceContainerError("Cleanup failed")
    response = await client.delete(f"workspaces/{workspace_id}?detach_sessions=true")
    assert response.status_code == 502
    async with factory() as db:
        assert await db.get(Workspace, workspace_id)
        assert (await db.get(Session, first.id)).workspace_id == workspace_id
    containers.stop.side_effect = None
    assert (
        await client.delete(f"workspaces/{workspace_id}?detach_sessions=true")
    ).status_code == 204


@pytest.mark.asyncio
async def test_start_waits_for_removal_and_observes_detached_session(workspace_app):
    client, factory, _, first, second, registry = workspace_app
    workspace_id, _ = await linked_workspace(workspace_app)
    deleting, proceed, ran = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def stop(*args, **kwargs):
        deleting.set()
        await proceed.wait()

    async def agent():
        ran.set()
        async with factory() as db:
            assert (await db.get(Session, first.id)).workspace_id is None

    containers.stop.side_effect = stop
    removal = asyncio.create_task(client.delete(f"workspaces/{workspace_id}?detach_sessions=true"))
    await asyncio.wait_for(deleting.wait(), 2)
    starting = asyncio.create_task(registry.start(str(first.id), agent()))
    attaching = asyncio.create_task(
        client.put(f"sessions/{second.id}/workspace", json={"workspace_id": str(workspace_id)})
    )
    await asyncio.sleep(0)
    assert not ran.is_set() and not attaching.done()
    proceed.set()
    assert (await removal).status_code == 204
    run = await starting
    await run
    assert (await attaching).status_code == 404


@pytest.mark.asyncio
async def test_registry_rejects_duplicate_without_starting_it():
    registry = AgentRunRegistry()
    first = await registry.start("one", asyncio.sleep(60))
    ran = False

    async def duplicate():
        nonlocal ran
        ran = True

    try:
        assert await registry.start("one", duplicate()) is None
        assert not ran
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
