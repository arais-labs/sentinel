from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.engine import create_database_engine
from app.models import Base, Session, SessionRuntimeCleanup, Workspace
from app.services.runtime import session_cleanup as cleanup
from app.services.runtime.container_transport import RunningContainerTransport
from app.services.runtime.terminal_manager import RuntimeTerminalManager
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.service import SessionService


@pytest.mark.asyncio
async def test_offline_delete_persists_cleanup_and_retries_after_restart(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path}/instance.sqlite"
    engine = create_database_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = SessionService(run_registry=AgentRunRegistry())
    async with factory() as db:
        workspace = Workspace(
            id=uuid4(),
            name="shared",
            machine_id=uuid4(),
            directory="/project",
            development_tools=[],
        )
        session = Session(id=uuid4(), user_id="local", workspace_id=workspace.id)
        sibling = Session(id=uuid4(), user_id="local", workspace_id=workspace.id)
        child = Session(id=uuid4(), user_id="local", parent_session_id=session.id)
        db.add(workspace)
        await db.flush()
        db.add_all([session, sibling])
        await db.flush()
        db.add(child)
        await db.commit()
        assert await service.delete_session(db, session_id=session.id, user_id="local") == 1
    await engine.dispose()

    # Reopen the database: no in-memory session binding is needed for cleanup.
    engine = create_database_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    from app.services.modules import runtime_services
    from app.services.runtime import ssh_runtime

    pool = type("Pool", (), {"remove": AsyncMock()})()
    monkeypatch.setattr(runtime_services, "get_browser_pool", lambda: pool)
    monkeypatch.setattr(ssh_runtime, "invalidate_runtime_for_session", AsyncMock())
    states = AsyncMock(return_value={str(workspace.id): {"state": "failed"}})
    monkeypatch.setattr(cleanup.containers, "statuses", states)
    run = AsyncMock(return_value=type("Result", (), {"exit_status": 0})())
    monkeypatch.setattr(RunningContainerTransport, "run", run)
    delete = AsyncMock(side_effect=RuntimeError("runtime disconnected"))
    monkeypatch.setattr(RuntimeTerminalManager, "delete_session_state", delete)
    await cleanup.drain_cleanup(factory, "main")
    delete.assert_not_awaited()
    async with factory() as db:
        assert await db.get(Session, session.id) is None
        assert await db.get(Session, child.id) is None
        assert await db.get(Session, sibling.id) is not None
        assert await db.get(Workspace, workspace.id) is not None
        assert len(list(await db.scalars(select(SessionRuntimeCleanup)))) == 2
    states.return_value = {str(workspace.id): {"state": "running"}}
    await cleanup.drain_cleanup(factory, "main")
    async with factory() as db:
        assert len(list(await db.scalars(select(SessionRuntimeCleanup)))) == 2
    delete.side_effect = None
    await cleanup.drain_cleanup(factory, "main")
    assert {call.args[0] for call in delete.await_args_list} == {
        str(session.id),
        str(child.id),
    }
    async with factory() as db:
        assert list(await db.scalars(select(SessionRuntimeCleanup))) == []
        assert await db.get(Session, sibling.id) is not None
        assert await db.get(Workspace, workspace.id) is not None
    await engine.dispose()


@pytest.mark.asyncio
async def test_cleanup_transport_never_starts_offline_workspace(monkeypatch):
    monkeypatch.setattr(RunningContainerTransport, "is_ready", AsyncMock(return_value=False))
    start = AsyncMock()
    monkeypatch.setattr(cleanup.containers, "start", start)
    transport = RunningContainerTransport(uuid4(), "/project", [])
    with pytest.raises(cleanup.containers.WorkspaceContainerError):
        await transport.run("true")
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_and_cleanup_queue_roll_back_together(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path}/instance.sqlite")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        workspace = Workspace(
            id=uuid4(),
            name="shared",
            machine_id=uuid4(),
            directory="/project",
            development_tools=[],
        )
        db.add(workspace)
        await db.flush()
        session = Session(id=uuid4(), user_id="local", workspace_id=workspace.id)
        db.add(session)
        await db.commit()
        session_id = session.id
        monkeypatch.setattr(db, "commit", AsyncMock(side_effect=RuntimeError("commit failed")))
        with pytest.raises(RuntimeError, match="commit failed"):
            await SessionService(run_registry=AgentRunRegistry()).delete_session(
                db,
                session_id=session_id,
                user_id="local",
            )
        await db.rollback()
    async with factory() as db:
        assert await db.get(Session, session_id) is not None
        assert list(await db.scalars(select(SessionRuntimeCleanup))) == []
    await engine.dispose()
