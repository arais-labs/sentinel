"""[Workspace Report]: the agent that started preparing a workspace hears when it settles."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.models import Message, Session
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.workspaces import lifecycle, reports
from tests.fake_db import FakeDB


@pytest.fixture
def world(monkeypatch):
    """One chat, a run registry that records wakeups, and a scripted sequence of states."""
    monkeypatch.setattr(reports, "POLL_SECONDS", 0)
    db = FakeDB()
    session = Session(user_id="local", title="main", status="active")
    db.add(session)
    registry = AgentRunRegistry()
    wakeups = []

    async def wakeup(sid):
        wakeups.append(sid)

    registry.configure_idle_interjections_callback(wakeup)
    workspace_id = uuid4()
    script = []

    async def statuses(asked):
        assert asked == workspace_id
        step = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(step, Exception):
            raise step
        return {str(workspace_id): step}

    monkeypatch.setattr(lifecycle, "statuses", statuses)

    @asynccontextmanager
    async def factory():
        yield db

    def watch():
        return reports.watch(
            session_id=session.id,
            workspace_id=workspace_id,
            name="Box",
            db_factory=factory,
            run_registry=registry,
        )

    async def finished():
        await asyncio.gather(*list(reports._watches.values()))

    yield type(
        "World",
        (),
        dict(
            db=db,
            session=session,
            registry=registry,
            wakeups=wakeups,
            workspace_id=workspace_id,
            script=script,
            watch=staticmethod(watch),
            finished=staticmethod(finished),
        ),
    )
    reports._watches.clear()


@pytest.mark.asyncio
async def test_running_delivers_one_report_and_wakes_the_idle_agent(world):
    world.script.extend([{"state": "preparing"}, {"state": "preparing"}, {"state": "running"}])
    assert world.watch()
    # A second create/start while it prepares does not report twice.
    assert not world.watch()
    await world.finished()
    [item] = world.registry.drain_interjections(str(world.session.id))
    text = item.content[0].text
    assert item.role == "user" and text.startswith("[Workspace Report]")
    assert f"Workspace ID: {world.workspace_id}" in text and "Workspace: Box" in text
    assert "State: running" in text and "workspaces.log" not in text
    assert item.metadata["notice"]["title"] == "Workspace report"
    assert item.metadata["source"] == "workspace_preparation"
    assert item.metadata["steering"] == "pending"
    assert world.wakeups == [str(world.session.id)]
    assert len(world.db.storage[Message]) == 1
    assert not reports._watches


@pytest.mark.asyncio
async def test_failed_reports_the_error_and_where_to_look(world):
    failed = {"state": "failed", "error": "install.sh exited with 1"}
    world.script.extend([{"state": "preparing"}, failed, failed])
    world.watch()
    await world.finished()
    [item] = world.registry.drain_interjections(str(world.session.id))
    text = item.content[0].text
    assert "State: failed" in text and "install.sh exited with 1" in text
    assert "workspaces.log" in text
    assert item.metadata["status"] == "failed"


@pytest.mark.asyncio
async def test_a_recovery_settles_once_it_stops(world):
    stopped = {"state": "stopped"}
    world.script.extend([{"state": "recovering"}, {"state": "recovering"}, stopped, stopped])
    world.watch()
    await world.finished()
    [item] = world.registry.drain_interjections(str(world.session.id))
    assert "State: stopped" in item.content[0].text
    assert "start it to use it" in item.content[0].text


@pytest.mark.asyncio
async def test_a_single_unreachable_read_is_not_the_end(world):
    # A slow remote Mac answers once with an error: the watch goes on until it runs.
    world.script.extend([TimeoutError(), {"state": "preparing"}, {"state": "running"}])
    world.watch()
    await world.finished()
    [item] = world.registry.drain_interjections(str(world.session.id))
    assert "State: running" in item.content[0].text


@pytest.mark.asyncio
async def test_an_unreachable_runtime_settles_as_unavailable(world):
    world.script.append(lifecycle.WorkspaceLifecycleError("Could not reach workspace services."))
    world.watch()
    await world.finished()
    [item] = world.registry.drain_interjections(str(world.session.id))
    assert "State: unavailable" in item.content[0].text
    assert "Could not reach workspace services." in item.content[0].text


@pytest.mark.asyncio
async def test_the_report_is_idempotent_per_preparation(world):
    state = {"state": "running"}
    args = (world.db, world.session, world.workspace_id, "Box", "prep-1", state)
    options = {"run_registry": world.registry, "wake": True}
    assert await reports.deliver(*args, **options)
    assert not await reports.deliver(*args, **options)
    # The next preparation of the same workspace reports again.
    assert await reports.deliver(*args[:4], "prep-2", state, **options)
    assert len(world.registry.drain_interjections(str(world.session.id))) == 2


@pytest.mark.asyncio
async def test_the_watch_gives_up_after_its_cap(world, monkeypatch):
    monkeypatch.setattr(reports, "LIMIT_SECONDS", 0)
    world.script.append({"state": "preparing"})
    world.watch()
    await world.finished()
    [item] = world.registry.drain_interjections(str(world.session.id))
    assert "Still preparing after" in item.content[0].text


@pytest.mark.asyncio
async def test_a_finished_sub_agent_is_not_woken(world):
    child = Session(
        user_id="local", title="sub", status="active", parent_session_id=world.session.id
    )
    world.db.add(child)
    world.script.append({"state": "running"})

    @asynccontextmanager
    async def factory():
        yield world.db

    reports.watch(
        session_id=child.id,
        workspace_id=world.workspace_id,
        name="Box",
        db_factory=factory,
        run_registry=world.registry,
    )
    await world.finished()
    assert len(world.registry.peek_interjections(str(child.id))) == 1
    assert not world.wakeups


@pytest.mark.asyncio
async def test_shutdown_cancels_the_watches(world):
    world.script.append({"state": "preparing"})
    world.watch()
    await reports.close()
    assert not reports._watches
    assert not world.registry.peek_interjections(str(world.session.id))
