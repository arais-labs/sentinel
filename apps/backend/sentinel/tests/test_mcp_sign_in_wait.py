"""An MCP call whose server needs sign-in waits for the user, then runs; or fails cleanly in time."""

import asyncio
import gc
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sentral.errors import ToolValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.mcp import MCPServer
from app.routers.mcp import public_server
from app.schemas.mcp import MCPConnection
from app.services.mcp import connections, signin
from app.services.mcp.errors import SignInRequired
from app.services.mcp.tools import server_tools
from app.services.tools.registry import ToolRuntimeContext


@pytest_asyncio.fixture
async def setup(tmp_path, monkeypatch):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "signin.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    server = MCPServer(
        id="b" * 32,
        name="Broker",
        enabled=True,
        config=MCPConnection(
            url="https://mcp.example.com/mcp", oauth={"tokens": {}}
        ).model_dump_json(),
        tools=[{"name": "positions", "inputSchema": {"type": "object", "properties": {}}}],
    )
    async with factory() as db:
        db.add(server)
        await db.commit()
    notify = AsyncMock()
    monkeypatch.setattr(signin, "_notify", notify)
    yield factory, server, server_tools(server, factory)[0], notify
    await engine.dispose()


async def stored(factory, server):
    async with factory() as db:
        return public_server(await db.get(MCPServer, server.id))


@pytest.mark.asyncio
async def test_call_waits_for_sign_in_then_runs(setup, monkeypatch):
    factory, server, tool, notify = setup
    call = AsyncMock(
        side_effect=[SignInRequired("expired"), {"content": [{"type": "text", "text": "ok"}]}]
    )
    monkeypatch.setattr(connections, "call", call)
    pending = asyncio.create_task(tool.execute({}, ToolRuntimeContext(instance_name="main")))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if (await stored(factory, server))["sign_in_required"]:
            break
    view = await stored(factory, server)
    assert (
        view["sign_in_required"] and not view["authenticated"]
    )  # Settings no longer says connected
    assert not pending.done()  # the agent's call is waiting, not failed
    notify.assert_awaited_once()
    await signin.completed(factory, server.id)  # the user signed in
    result = await asyncio.wait_for(pending, 5)
    assert result["content"][0]["text"] == "ok" and call.await_count == 2
    assert not (await stored(factory, server))["sign_in_required"]


@pytest.mark.asyncio
async def test_call_fails_cleanly_when_nobody_signs_in(setup, monkeypatch):
    factory, server, tool, _ = setup
    monkeypatch.setattr(signin, "WAIT_SECONDS", 0.2)
    monkeypatch.setattr(connections, "call", AsyncMock(side_effect=SignInRequired("expired")))
    with pytest.raises(ToolValidationError, match="Broker needs you to sign in"):
        await tool.execute({}, ToolRuntimeContext())


@pytest.mark.asyncio
async def test_wait_that_times_out_leaves_nothing_behind(setup, monkeypatch):
    factory, server, _, _ = setup
    monkeypatch.setattr(signin, "WAIT_SECONDS", 0.05)
    # In its own task, like a call that gave up: a running task keeps its last timeout's traceback.
    assert await asyncio.create_task(signin.wait(factory, server.id, server.name)) is False
    gc.collect()
    assert not signin._completed
