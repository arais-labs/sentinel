"""Load MCP servers into a session on demand instead of carrying every schema each turn."""

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.models.mcp import MCPServer
from app.services.mcp import exposure
from app.services.mcp.tools import action_ids, server_namespaces
from app.services.modules.definitions import ActionDefinition, ModuleDefinition
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.runtime_context import require_session_id

CATALOG_TOOL_PREFIX = "catalog_"


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server: str = Field(
        min_length=1, max_length=32, description="Server id from the MCP server list."
    )


async def describe(payload: dict, runtime: ToolRuntimeContext) -> dict:
    if runtime.db_session_factory is None:
        raise ValueError("Catalog requires a database context")
    target = Target.model_validate(payload)
    async with runtime.db_session_factory() as db:
        server = await db.get(MCPServer, target.server)
        if server is None or not server.enabled:
            raise ValueError(f"Unknown or disabled MCP server {target.server!r}")
        tools = [
            {"id": tool_id, "description": (tool.get("description") or tool["name"])[:200]}
            for tool, tool_id in zip(server.tools, action_ids(server.tools), strict=True)
        ]
    return {"server": server.id, "name": server.name, "tools": tools}


async def load(payload: dict, runtime: ToolRuntimeContext) -> dict:
    if runtime.db_session_factory is None:
        raise ValueError("Catalog requires a database context")
    session_id = require_session_id(runtime)
    target = Target.model_validate(payload)
    async with runtime.db_session_factory() as db:
        server = await db.get(MCPServer, target.server)
        if server is None or not server.enabled:
            raise ValueError(f"Unknown or disabled MCP server {target.server!r}")
        await exposure.load(db, session_id, server.id)
        enabled = (
            (await db.execute(select(MCPServer).where(MCPServer.enabled.is_(True)))).scalars().all()
        )
        namespace = server_namespaces(enabled)[server.id]
        names = [f"{namespace}_{tool_id}" for tool_id in action_ids(server.tools)]
    return {
        "loaded": names,
        "note": "These tools are now in your tool list for this conversation. Call them directly.",
    }


MODULE = ModuleDefinition(
    name="catalog",
    label="MCP catalog",
    description="Loads MCP server tools into the conversation on demand.",
    system=True,
    actions=[
        ActionDefinition(
            id="describe",
            label="List a server's tools",
            description=(
                "List the tools an MCP server offers (names and one-line descriptions, no schemas). "
                "Only needed when the server list leaves you unsure whether a server can do the task; "
                "otherwise call catalog_load directly."
            ),
            handler=describe,
            requires_runtime_context=True,
            permission_default="allow",
            parameters_schema=Target.model_json_schema(),
        ),
        ActionDefinition(
            id="load",
            label="Load a server's tools",
            description=(
                "Load all of an MCP server's tools into this conversation so you can call them. "
                "They stay available for the rest of the conversation."
            ),
            handler=load,
            requires_runtime_context=True,
            permission_default="allow",
            parameters_schema=Target.model_json_schema(),
        ),
    ],
)
