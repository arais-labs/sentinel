"""An expired on-demand MCP server called from memory reloads itself and explains bad arguments."""

from uuid import uuid4

import pytest
from sentral.errors import ToolValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.mcp import MCPServer
from app.models.sessions import Session
from app.schemas.mcp import MCPConnection
from app.services.agent_runtime_adapters.tools import SentinelToolRegistryAdapter
from app.services.mcp import exposure
from app.services.mcp.tools import server_tools
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRegistry, ToolRuntimeContext

SNAPSHOT = {
    "name": "get_price_snapshot",
    "inputSchema": {
        "type": "object",
        "properties": {
            "contract_id": {
                "type": "integer",
                "description": "Contract identifier.\nPass as a JSON number, unquoted - e.g. 265598.",
            }
        },
    },
}


@pytest.mark.asyncio
async def test_tool_called_from_memory_reloads_its_server_and_explains_the_type(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "memory.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_id = uuid4()
    broker = MCPServer(
        id="d" * 32,
        name="Broker",
        enabled=True,
        config=MCPConnection(url="https://broker.example.test/mcp").model_dump_json(),
        tools=[SNAPSHOT],
    )
    try:
        async with factory() as db:
            db.add_all([Session(id=session_id, user_id="local"), broker])
            await db.commit()
        registry = ToolRegistry()
        for definition in server_tools(broker, factory):
            registry.register(definition)
        async with factory() as db:
            state = await exposure.refresh(db, session_id)
        executor = ToolExecutor(registry)
        adapter = SentinelToolRegistryAdapter(registry, executor, exposure=state)
        assert adapter.list_tools() == []  # expired: not in the model's tool list
        runtime = ToolRuntimeContext(session_id=session_id, db_session_factory=factory)
        with pytest.raises(ToolValidationError) as error:
            await executor.execute(
                "smcp_broker_get_price_snapshot", {"contract_id": "45540822"}, runtime=runtime
            )
        # The schema's own guidance comes back with the error...
        assert "must be an integer" in str(error.value) and "JSON number, unquoted" in str(
            error.value
        )
        # ...and the server is loaded again, so the next step shows the model its schema.
        assert [t.name for t in adapter.list_tools()] == ["smcp_broker_get_price_snapshot"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_parallel_calls_to_an_unloaded_server_all_run(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from app.services.mcp import connections

    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "parallel.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_id = uuid4()
    broker = MCPServer(
        id="e" * 32,
        name="Broker",
        enabled=True,
        config=MCPConnection(url="https://broker.example.test/mcp").model_dump_json(),
        tools=[SNAPSHOT],
    )
    try:
        async with factory() as db:
            db.add_all([Session(id=session_id, user_id="local"), broker])
            await db.commit()
        registry = ToolRegistry()
        for definition in server_tools(broker, factory):
            registry.register(definition)
        registry.get("smcp_broker_get_price_snapshot").approval_check = None
        monkeypatch.setattr(
            connections,
            "call",
            AsyncMock(return_value={"content": [{"type": "text", "text": "ok"}]}),
        )
        executor = ToolExecutor(registry)

        def runtime():
            return ToolRuntimeContext(
                session_id=session_id, db_session_factory=factory, agent_mode="full_permission"
            )

        results = await asyncio.gather(
            *(
                executor.execute(
                    "smcp_broker_get_price_snapshot", {"contract_id": 1}, runtime=runtime()
                )
                for _ in range(3)
            )
        )
        assert all(result["content"][0]["text"] == "ok" for result, _ in results)
    finally:
        await engine.dispose()
