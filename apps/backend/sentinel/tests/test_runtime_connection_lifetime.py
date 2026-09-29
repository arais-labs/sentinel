"""Slow runtime requests and idle streams must not starve unrelated DB work."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.models import Base, Session, Workspace
from app.models.manager import ManagerBase, SentinelInstance
from app.services.runtime import control


@pytest_asyncio.fixture
async def database(tmp_path):
    # One connection exposes nested checkouts and retained read transactions.
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/pool.sqlite",
        pool_size=1,
        max_overflow=0,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(ManagerBase.metadata.create_all)
    async with factory() as db:
        workspace = Workspace(name="test", machine_id=uuid4(), directory="/project")
        db.add(workspace)
        await db.flush()
        session = Session(user_id="local", workspace_id=workspace.id)
        db.add(session)
        db.add(SentinelInstance(name="test", database_name="test"))
        await db.commit()
    try:
        yield engine, factory, session.id, workspace.id
    finally:
        await engine.dispose()


def request():
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/instances/test/runtime/live-view",
            "headers": [],
            "path_params": {"instance_name": "test"},
        }
    )


@pytest.mark.asyncio
async def test_desktop_request_releases_lookup_connection_before_runtime(database, monkeypatch):
    engine, factory, sid, _ = database

    async def get_manager(**kwargs):
        # Check ownership at the handoff, not how quickly concurrent tasks run.
        assert engine.pool.checkedout() == 0
        # Runtime resolution needs its own connection to resolve the binding.
        async with factory() as db:
            assert await db.get(Session, sid) is not None
        return SimpleNamespace(enabled=True, status=runtime_status)

    async def runtime_status():
        # Unrelated DB work must succeed before the runtime request completes.
        assert engine.pool.checkedout() == 0
        async with factory() as db:
            assert await db.scalar(select(Session.id)) == sid
        return {"state": "stopped"}

    monkeypatch.setattr(control, "runtime_configured", AsyncMock(return_value=True))
    monkeypatch.setattr(control, "get_runtime_desktop_manager", get_manager)

    async with factory() as db:
        result = await control.live_view_response(
            request=request(),
            session_id=str(sid),
            db=db,
            geometry=None,
            resolution_presets={"1920x1200"},
        )
        assert result.state == "stopped"
        assert engine.pool.checkedout() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["resolution", "action", "desktop_socket", "forward_socket"])
async def test_runtime_handoffs_release_lookup_connection(database, monkeypatch, route):
    from app.routers import sessions

    engine, factory, sid, _ = database

    async def runtime_probe(**kwargs):
        assert engine.pool.checkedout() == 0
        async with factory() as db:
            assert await db.get(Session, sid) is not None
        # Stop at the handoff without starting a real runtime or socket stream.
        raise asyncio.CancelledError

    monkeypatch.setattr(control, "runtime_configured", AsyncMock(return_value=True))
    monkeypatch.setattr(control, "get_runtime_desktop_manager", runtime_probe)
    monkeypatch.setattr(sessions, "get_runtime_port_forward_manager", runtime_probe)
    socket = SimpleNamespace(path_params={"instance_name": "test"}, close=AsyncMock())
    async with factory() as db:
        if route == "resolution":
            operation = control.set_live_view_resolution_response(
                request=request(),
                session_id=str(sid),
                db=db,
                geometry="1920x1200",
                resolution_presets={"1920x1200"},
            )
        elif route == "action":
            monkeypatch.setattr(control, "runtime_configured", runtime_probe)
            operation = control.require_runtime_session(str(sid), instance_name="test", db=db)
        elif route == "desktop_socket":
            operation = control.bridge_runtime_desktop_stream(
                websocket=socket, session_id=sid, db=db
            )
        else:
            operation = sessions.proxy_runtime_forward_websocket(
                websocket=socket,
                id=sid,
                forward_id="test",
                db=db,
            )
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert engine.pool.checkedout() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["browse", "list", "metrics"])
async def test_workspace_reads_release_connection_before_runtime(database, monkeypatch, route):
    from app.routers import workspace_browser, workspaces

    engine, factory, _, wid = database

    async def runtime_probe(*args, **kwargs):
        assert engine.pool.checkedout() == 0
        async with factory() as other:
            assert await other.get(Workspace, wid) is not None
        raise asyncio.CancelledError

    monkeypatch.setattr(workspaces.containers, "overview", runtime_probe)
    monkeypatch.setattr(workspaces.containers, "statuses", runtime_probe)
    monkeypatch.setattr(workspaces.workspace_metrics, "get", runtime_probe)
    async with factory() as db:
        if route == "browse":
            operation = workspace_browser.browse_workspace(wid, "files", limit=500, db=db)
        elif route == "list":
            operation = workspaces.list_workspaces(db=db)
        else:
            operation = workspaces.get_workspace_metrics(wid, db=db)
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert engine.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_instance_lookup_returns_manager_connection_immediately(database, monkeypatch):
    from app import dependencies

    engine, factory, _, _ = database
    monkeypatch.setattr(dependencies, "ManagerSessionLocal", factory)
    instance = await dependencies.get_instance_record("test")
    assert instance.database_name == "test"
    assert engine.pool.checkedout() == 0
