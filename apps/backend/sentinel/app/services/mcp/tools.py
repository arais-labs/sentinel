"""Expose discovered MCP tools through Sentinel's existing approval pipeline."""

import hashlib
import re
from sentral.errors import ToolValidationError
from sqlalchemy import select
from app.models.mcp import MCPServer
from app.schemas.mcp import MCPConnection
from app.services.mcp import connections, exposure
from app.services.modules.definitions import ActionDefinition, ModuleDefinition
from app.services.modules.tool_adapter import build_module_tools
from app.services.tools.registry import ToolRuntimeContext


def _slug(value: str, *, limit: int) -> str:
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", words).strip("_").lower()
    return (normalized or "tool")[:limit].rstrip("_")


def server_namespaces(servers: list[MCPServer]) -> dict[str, str]:
    """Return readable namespaces, adding an ID only when names collide."""
    bases = {server.id: f"smcp_{_slug(server.name, limit=24)}" for server in servers}
    counts: dict[str, int] = {}
    for base in bases.values():
        counts[base] = counts.get(base, 0) + 1
    return {
        server.id: (
            bases[server.id]
            if counts[bases[server.id]] == 1
            else f"{bases[server.id]}_{server.id[:6]}"
        )
        for server in servers
    }


def action_ids(tools: list[dict]) -> list[str]:
    """Keep remote tool names legible while resolving normalized collisions."""
    bases = [_slug(str(tool["name"]), limit=30) for tool in tools]
    counts: dict[str, int] = {}
    for base in bases:
        counts[base] = counts.get(base, 0) + 1
    return [
        (
            base
            if counts[base] == 1
            else f"{base}_{hashlib.sha256(str(tool['name']).encode()).hexdigest()[:8]}"
        )
        for base, tool in zip(bases, tools, strict=True)
    ]


def permission_actions(server: MCPServer, namespace: str) -> list[str]:
    return [f"{namespace}.{action_id}" for action_id in action_ids(server.tools)]


def server_tools(server: MCPServer, session_factory, *, namespace: str | None = None):
    actions = []
    namespace = namespace or f"smcp_{_slug(server.name, limit=24)}"
    for tool, tool_id in zip(server.tools, action_ids(server.tools), strict=True):
        remote_name = tool["name"]

        async def execute(arguments, runtime: ToolRuntimeContext, name=remote_name):
            # Re-read before execution so disabling/removing a server takes effect
            # even for a previously assembled agent registry.
            async with session_factory() as db:
                current = await db.get(MCPServer, server.id)
                if current is None or not current.enabled:
                    raise PermissionError("This MCP server is disabled or removed")
                config = MCPConnection.model_validate_json(current.config)
            try:
                result = await connections.call(
                    session_factory,
                    server.id,
                    runtime.runtime_session_id or runtime.session_id,
                    config,
                    name,
                    arguments,
                )
            except Exception:
                # Transport errors can contain authorization headers or URL secrets.
                raise RuntimeError(
                    "MCP call failed. Check the server connection; the call was not retried."
                ) from None
            if result.get("isError"):
                message = "\n".join(
                    block.get("text", "")
                    for block in result.get("content", [])
                    if block.get("type") == "text"
                )
                raise ToolValidationError(message[:4000] or "MCP tool reported an error")
            if runtime.session_id is not None and not server.always_load:
                async with session_factory() as db:
                    await exposure.touch(db, runtime.session_id, server.id)
            return result

        actions.append(
            ActionDefinition(
                id=tool_id,
                label=remote_name,
                description=f"{server.name} / {remote_name}: {tool.get('description') or remote_name}",
                parameters_schema=tool.get("inputSchema") or {"type": "object", "properties": {}},
                handler=execute,
                requires_runtime_context=True,
                permission_default="approval",
            )
        )
    module = ModuleDefinition(name=namespace, label=server.name, actions=actions)
    definitions = build_module_tools(module, session_factory=session_factory)
    for definition, action in zip(definitions, actions, strict=True):
        definition.name = f"{module.name}_{action.id}"
        definition.deferred = not server.always_load
        definition.server_id = server.id
    return definitions


async def load_tools(session_factory):
    async with session_factory() as db:
        servers = (
            (await db.execute(select(MCPServer).where(MCPServer.enabled.is_(True)))).scalars().all()
        )
    namespaces = server_namespaces(servers)
    return [
        tool
        for server in servers
        for tool in server_tools(server, session_factory, namespace=namespaces[server.id])
    ]
