import asyncio
import socket
import sys
import uvicorn
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
import pytest
from mcp import Client, types
from mcp.server import MCPServer as ProtocolServer
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.dependencies import get_db
from app.routers import mcp as mcp_router
from app.models.base import Base
from app.models.mcp import MCPServer, MCPSessionExposure
from app.models.modules import ModulePermission
from app.routers.module_permissions import _resolved_permissions
from app.routers.mcp import public_server
from app.schemas.mcp import MCPConnection
from app.services.mcp import client, connections
from app.services.mcp.tools import action_ids, server_tools
from app.services.tools.registry import ToolApprovalDecision, ToolRuntimeContext, ToolRegistry


@pytest.mark.parametrize(
    "config",
    [
        {"transport": "stdio"},
        {"url": "file:///tmp/server"},
        {"url": "https://user:secret@example.test/mcp"},
        {"url": "https://example.test/mcp", "command": "unexpected"},
    ],
)
def test_reject_invalid_connection(config):
    with pytest.raises(ValidationError):
        MCPConnection(**config)


def test_mcp_action_names_are_readable_and_collision_safe():
    tools = [
        {"name": "getAccounts"},
        {"name": "list-transfer.money"},
        {"name": "same name"},
        {"name": "same-name"},
    ]
    names = action_ids(tools)
    assert names[:2] == ["get_accounts", "list_transfer_money"]
    assert names[2].startswith("same_name_")
    assert names[3].startswith("same_name_")
    assert names[2] != names[3]


@pytest.mark.asyncio
async def test_catalog_pagination_and_call_content(monkeypatch):
    session = SimpleNamespace(
        list_tools=AsyncMock(
            side_effect=[
                types.ListToolsResult(
                    tools=[types.Tool(name="first", input_schema={"type": "object"})],
                    next_cursor="next",
                ),
                types.ListToolsResult(
                    tools=[types.Tool(name="second", input_schema={"type": "object"})]
                ),
            ]
        ),
        call_tool=AsyncMock(
            return_value=types.CallToolResult(
                content=[types.TextContent(type="text", text="answer")],
                structured_content={"value": 42},
                is_error=False,
            )
        ),
    )

    @asynccontextmanager
    async def connect(config, auth=None):
        yield session

    monkeypatch.setattr(client, "connect", connect)
    config = MCPConnection(url="https://example.test/mcp")
    catalog = await client.discover(config)
    assert [tool["name"] for tool in catalog] == ["first", "second"]
    assert catalog[0]["inputSchema"] == {"type": "object"}
    assert session.list_tools.await_args_list[1].kwargs == {"cursor": "next"}
    result = await client.call(config, "first", {"query": "hello"})
    assert result["structuredContent"] == {"value": 42}
    assert result["content"][0]["text"] == "answer"
    session.call_tool.assert_awaited_once_with("first", {"query": "hello"})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_sdk_negotiates_and_executes_against_protocol_server(mode):
    server = ProtocolServer("Test server")

    @server.tool()
    def echo(value: str) -> str:
        return value

    async with Client(server, mode=mode) as session:
        tools = await session.list_tools()
        assert tools.tools[0].name == "echo"
        result = await session.call_tool("echo", {"value": "hello"})
        assert result.content[0].text == "hello"


@pytest.mark.asyncio
async def test_encrypted_credentials_approval_and_disable(tmp_path, monkeypatch):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "test.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    server = MCPServer(
        id="a" * 32,
        name="Test server",
        enabled=True,
        config=MCPConnection(
            url="https://example.test/mcp", headers={"Authorization": "Bearer test-only-value"}
        ).model_dump_json(),
        tools=[
            {
                "name": "echo",
                "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}},
            }
        ],
    )
    try:
        async with factory() as db:
            db.add(server)
            await db.commit()
            stored = (await db.execute(text("SELECT config FROM mcp_servers"))).scalar_one()
            assert "test-only-value" not in stored
            assert "test-only-value" not in str(public_server(server))
        tool = server_tools(server, factory)[0]
        assert tool.name == "smcp_test_server_echo"
        approval = await tool.approval_check()
        assert approval.decision == ToolApprovalDecision.REQUIRE
        async with factory() as db:
            permissions = await _resolved_permissions(db)
            assert permissions["smcp_test_server.echo"] == "approval"
            db.add(ModulePermission(action="smcp_test_server.echo", level="allow"))
            await db.commit()
        approval = await tool.approval_check()
        assert approval.decision == ToolApprovalDecision.ALLOW
        call = AsyncMock(return_value={"content": [{"type": "text", "text": "hello"}]})
        monkeypatch.setattr(connections, "call", call)
        await tool.execute({"value": "hello"}, ToolRuntimeContext())
        assert call.await_count == 1
        async with factory() as db:
            row = await db.get(MCPServer, server.id)
            row.enabled = False
            await db.commit()
        with pytest.raises(PermissionError):
            await tool.execute({"value": "hello"}, ToolRuntimeContext())
        assert call.await_count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
async def test_http_transports_discover_and_execute(transport):

    server = ProtocolServer("Transport test")

    @server.tool()
    def echo(value: str) -> str:
        return value

    app = server.sse_app() if transport == "sse" else server.streamable_http_app()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    process = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    task = asyncio.create_task(process.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not process.started:
                await asyncio.sleep(0.01)
        config = MCPConnection(
            transport="auto",
            url=f"http://127.0.0.1:{port}/" + ("sse" if transport == "sse" else "mcp"),
        )
        tools = await client.discover(config)
        assert tools[0]["name"] == "echo"
        result = await client.call(config, "echo", {"value": "transport works"})
        assert result["content"][0]["text"] == "transport works"
    finally:
        process.should_exit = True
        await task
        sock.close()


@pytest.mark.asyncio
async def test_stdio_transport_discover_and_execute(tmp_path):

    server = tmp_path / "server.py"
    server.write_text("""from mcp.server import MCPServer
server = MCPServer("Transport test")
@server.tool()
def echo(value: str) -> str:
    return value
server.run()
""")
    config = MCPConnection(transport="stdio", command=sys.executable, args=[str(server)])
    assert (await client.discover(config))[0]["name"] == "echo"
    result = await client.call(config, "echo", {"value": "stdio works"})
    assert result["content"][0]["text"] == "stdio works"


@pytest.mark.asyncio
async def test_connections_preserve_state_isolate_sessions_and_close(monkeypatch):
    opened = []
    closed = []

    @asynccontextmanager
    async def connect(config, auth=None):
        index = len(opened)
        count = 0

        async def invoke(name, arguments):
            nonlocal count
            count += 1
            return types.CallToolResult(content=[types.TextContent(type="text", text=str(count))])

        opened.append(index)
        try:
            yield SimpleNamespace(call_tool=invoke)
        finally:
            closed.append(index)

    monkeypatch.setattr(connections, "connect", connect)
    owner = object()
    config = MCPConnection(url="https://example.test/mcp")
    try:
        first = await connections.call(owner, "server", "session-a", config, "count", {})
        second = await connections.call(owner, "server", "session-a", config, "count", {})
        other = await connections.call(owner, "server", "session-b", config, "count", {})
        assert [r["content"][0]["text"] for r in [first, second, other]] == ["1", "2", "1"]
        assert len(opened) == 2
    finally:
        await connections.close(owner)
    assert sorted(closed) == [0, 1]


@pytest.mark.asyncio
async def test_connection_failure_does_not_retry_or_expose_credentials(monkeypatch):
    invoke = AsyncMock(side_effect=RuntimeError("Authorization: test-secret"))

    @asynccontextmanager
    async def connect(config, auth=None):
        yield SimpleNamespace(call_tool=invoke)

    monkeypatch.setattr(connections, "connect", connect)
    owner = object()
    try:
        with pytest.raises(RuntimeError, match="not retried") as error:
            await connections.call(
                owner,
                "server",
                "session",
                MCPConnection(url="https://example.test/mcp"),
                "write",
                {},
            )
        assert "test-secret" not in str(error.value)
        invoke.assert_awaited_once()
    finally:
        await connections.close(owner)


@pytest.mark.asyncio
async def test_settings_api_updates_existing_registry_and_hides_secrets(tmp_path, monkeypatch):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "api.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    registry = ToolRegistry()
    context = SimpleNamespace(session_factory=factory, tool_registry=registry)
    monkeypatch.setattr(mcp_router, "get_request_instance_runtime_context", lambda request: context)
    monkeypatch.setattr(
        client,
        "discover",
        AsyncMock(
            return_value=[
                {
                    "name": "echo",
                    "description": "Echo text",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]
        ),
    )
    app = FastAPI()
    app.include_router(mcp_router.router, prefix="/mcp")

    async def get_test_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = get_test_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as api:
            body = {
                "name": "Example",
                "connection": {
                    "url": "https://example.test/mcp",
                    "headers": {"Authorization": "test-secret"},
                },
            }
            created = await api.post("/mcp", json=body)
            assert created.status_code == 201
            server_id = created.json()["id"]
            assert "test-secret" not in created.text
            assert (await api.patch(f"/mcp/{server_id}", json={"enabled": True})).status_code == 400
            assert not registry.list_all()
            assert (await api.post(f"/mcp/{server_id}/test")).status_code == 200
            assert not registry.list_all()
            assert (await api.patch(f"/mcp/{server_id}", json={"enabled": True})).status_code == 200
            assert len(registry.list_all()) == 1
            assert "test-secret" not in (await api.get("/mcp")).text
            edited = await api.put(
                f"/mcp/{server_id}",
                json={"name": "Renamed", "connection": {"headers": {"Authorization": None}}},
            )
            assert edited.status_code == 200
            assert edited.json()["enabled"] is True
            assert edited.json()["connection"]["url"] == body["connection"]["url"]
            assert len(registry.list_all()) == 1
            async with factory() as db:
                saved = await db.get(MCPServer, server_id)
                assert (
                    MCPConnection.model_validate_json(saved.config).headers["Authorization"]
                    == "test-secret"
                )
            changed = {"name": "Renamed", "connection": {"url": "https://other.example.test/mcp"}}
            assert (await api.put(f"/mcp/{server_id}", json=changed)).status_code == 200
            assert not registry.list_all()
            assert (await api.delete(f"/mcp/{server_id}")).status_code == 204
            assert (await api.get("/mcp")).json() == []
    finally:
        await connections.close(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_on_demand_servers_stay_out_of_the_prompt_until_loaded(tmp_path):
    from uuid import uuid4

    from app.models.sessions import Message, Session
    from app.services.agent_runtime_adapters.tools import SentinelToolRegistryAdapter
    from app.services.mcp import exposure
    from app.services.modules.builtins.catalog.module import load as catalog_load
    from app.services.tools.executor import ToolExecutor

    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "exposure.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_id = uuid4()
    lazy = MCPServer(
        id="b" * 32,
        name="Lazy server",
        enabled=True,
        config=MCPConnection(url="https://lazy.example.test/mcp").model_dump_json(),
        tools=[{"name": "echo", "inputSchema": {"type": "object", "properties": {}}}],
    )
    pinned = MCPServer(
        id="c" * 32,
        name="Pinned server",
        enabled=True,
        always_load=True,
        config=MCPConnection(url="https://pinned.example.test/mcp").model_dump_json(),
        tools=[{"name": "ping", "inputSchema": {"type": "object", "properties": {}}}],
    )
    try:
        async with factory() as db:
            db.add_all([Session(id=session_id, user_id="local"), lazy, pinned])
            await db.commit()
        registry = ToolRegistry()
        for definition in server_tools(lazy, factory) + server_tools(pinned, factory):
            registry.register(definition)
        assert registry.get("smcp_lazy_server_echo").deferred is True
        assert registry.get("smcp_pinned_server_ping").deferred is False

        async with factory() as db:
            state = await exposure.refresh(db, session_id)
        adapter = SentinelToolRegistryAdapter(registry, ToolExecutor(registry), exposure=state)
        assert [t.name for t in adapter.list_tools()] == ["smcp_pinned_server_ping"]
        assert adapter.get_tool("smcp_lazy_server_echo") is not None  # executable when named
        assert "Lazy server (1 tools)" in exposure.summary_block(state)
        assert "Pinned server (1 tools) [loaded]" in exposure.summary_block(state)
        assert (
            registry.list_schemas(exposed=None) and len(registry.list_schemas(exposed=set())) == 1
        )

        runtime = ToolRuntimeContext(session_id=session_id, db_session_factory=factory)
        loaded = await catalog_load({"server": lazy.id}, runtime)
        assert loaded["loaded"] == ["smcp_lazy_server_echo"]
        # The same in-memory state the adapter holds: visible on the next iteration, no refresh.
        assert {t.name for t in adapter.list_tools()} == {
            "smcp_lazy_server_echo",
            "smcp_pinned_server_ping",
        }

        # Idle assistant messages expire the load; a pinned server never expires.
        async with factory() as db:
            for _ in range(exposure.EXPIRY_MESSAGES + 1):
                db.add(Message(session_id=session_id, role="assistant", content="..."))
            await db.commit()
            state = await exposure.refresh(db, session_id)
        assert state.loaded == set() and state.exposed == {pinned.id}

        await catalog_load({"server": lazy.id}, runtime)
        async with factory() as db:
            await exposure.clear(db, session_id)
            state = await exposure.refresh(db, session_id)
        assert state.loaded == set()

        # Rows of a session that never runs again are swept by any other session's refresh.
        from datetime import UTC, datetime

        other = uuid4()
        async with factory() as db:
            db.add(Session(id=other, user_id="local"))
            await db.commit()
        await catalog_load(
            {"server": lazy.id}, ToolRuntimeContext(session_id=other, db_session_factory=factory)
        )
        async with factory() as db:
            row = await db.get(MCPSessionExposure, (other, lazy.id))
            row.updated_at = datetime.now(UTC) - exposure.STALE_AFTER * 2
            await db.commit()
            await exposure.refresh(db, session_id)
            assert await db.get(MCPSessionExposure, (other, lazy.id)) is None
        exposure.forget(other)
    finally:
        exposure.forget(session_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_catalog_tools_hidden_without_servers_and_pin_via_api(tmp_path, monkeypatch):
    from uuid import uuid4

    from app.services.agent_runtime_adapters.tools import SentinelToolRegistryAdapter
    from app.services.mcp import exposure
    from app.services.tools.executor import ToolExecutor
    from app.services.tools.registry_builder import build_default_registry

    registry = build_default_registry()
    bare = SentinelToolRegistryAdapter(registry, ToolExecutor(registry))
    assert not {t.name for t in bare.list_tools()} & {"catalog_load", "catalog_describe"}
    state = exposure.SessionExposure(session_id=uuid4())
    adapter = SentinelToolRegistryAdapter(registry, ToolExecutor(registry), exposure=state)
    names = {t.name for t in adapter.list_tools()}
    assert "catalog_load" not in names and "catalog_describe" not in names
    state.enabled = {"x": object()}  # any enabled server brings the catalog tools back
    assert {"catalog_load", "catalog_describe"} <= {t.name for t in adapter.list_tools()}

    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "pin.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    context = SimpleNamespace(session_factory=factory, tool_registry=ToolRegistry())
    monkeypatch.setattr(mcp_router, "get_request_instance_runtime_context", lambda request: context)
    app = FastAPI()
    app.include_router(mcp_router.router, prefix="/mcp")

    async def get_test_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = get_test_db
    try:
        async with factory() as db:
            db.add(
                MCPServer(
                    id="d" * 32,
                    name="Pin me",
                    enabled=True,
                    config=MCPConnection(url="https://pin.example.test/mcp").model_dump_json(),
                    tools=[{"name": "echo", "inputSchema": {"type": "object", "properties": {}}}],
                )
            )
            await db.commit()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as api:
            listed = (await api.get("/mcp")).json()[0]
            assert listed["always_load"] is False
            pinned = await api.patch(f"/mcp/{'d' * 32}", json={"always_load": True})
            assert pinned.status_code == 200 and pinned.json()["always_load"] is True
            assert pinned.json()["enabled"] is True
            [tool] = context.tool_registry.list_all()
            assert tool.deferred is False
            unpinned = await api.patch(f"/mcp/{'d' * 32}", json={"always_load": False})
            assert unpinned.json()["always_load"] is False
            [tool] = context.tool_registry.list_all()
            assert tool.deferred is True
    finally:
        await connections.close(factory)
        await engine.dispose()
