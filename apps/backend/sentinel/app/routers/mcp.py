"""Instance MCP configuration. Credentials never appear in API responses."""

from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_db, get_request_instance_runtime_context
from app.models.mcp import MCPServer
from app.schemas.mcp import MCPConnection, MCPServerCreate, MCPServerToggle, MCPServerEdit
from app.services.mcp import client, connections, oauth
from app.services.mcp.errors import connection_message
from app.services.mcp.tools import load_tools

router = APIRouter()


def public_server(server):
    config = MCPConnection.model_validate_json(server.config)
    return {
        "id": server.id,
        "name": server.name,
        "transport": config.transport,
        "enabled": server.enabled,
        "always_load": server.always_load,
        "authenticated": bool(config.oauth.get("tokens")),
        "connection": {
            "transport": "stdio" if config.transport == "stdio" else "auto",
            "url": config.url,
            "command": config.command,
            "args": config.args,
            "headers": {key: None for key in config.headers},
            "env": {key: None for key in config.env},
            "timeout_seconds": config.timeout_seconds,
        },
        "tools": [
            {"name": t["name"], "description": t.get("description", "")} for t in server.tools
        ],
    }


async def get_server(db, server_id):
    server = await db.get(MCPServer, server_id)
    if server is None:
        raise HTTPException(404, "MCP server not found")
    return server


async def refresh_tools(request, server_id=None):
    context = get_request_instance_runtime_context(request)
    if server_id is not None:
        await connections.close(context.session_factory, server_id)
    tools = await load_tools(context.session_factory)
    # Existing executors hold this registry: update it without restarting agents.
    context.tool_registry.replace_namespace("smcp_", tools)


@router.get("")
async def list_servers(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(MCPServer).order_by(MCPServer.name))).scalars().all()
    return [public_server(row) for row in rows]


@router.post("", status_code=201)
async def create_server(body: MCPServerCreate, db: AsyncSession = Depends(get_db)):
    server = MCPServer(
        id=uuid4().hex,
        name=body.name.strip(),
        config=body.connection.model_dump_json(),
        enabled=False,
        tools=[],
    )
    db.add(server)
    await db.commit()
    return public_server(server)


def merge_connection(existing, changes):
    data = existing.model_dump()
    changed = changes.model_dump(exclude_unset=True)
    for field in ("headers", "env"):
        if field in changed:
            values = changed.pop(field)
            if values is not None:
                data[field] = {
                    key: value if value is not None else data[field][key]
                    for key, value in values.items()
                    if value is not None or key in data[field]
                }
    data.update({key: value for key, value in changed.items() if value is not None})
    if data["url"] != existing.url or data["transport"] == "stdio":
        data["oauth"] = {}
    # Secrets cannot silently follow a change of remote endpoint.
    if data["url"] != existing.url:
        explicit = changes.headers or {}
        data["headers"] = {key: value for key, value in explicit.items() if value is not None}
    return MCPConnection.model_validate(data)


@router.put("/{server_id}")
async def edit_server(
    server_id: str, body: MCPServerEdit, request: Request, db: AsyncSession = Depends(get_db)
):
    server = await get_server(db, server_id)
    old = MCPConnection.model_validate_json(server.config)
    try:
        updated = merge_connection(old, body.connection)
    except ValueError:
        raise HTTPException(
            422, "Check the connection address, command, and authentication fields"
        ) from None
    old_normalized = old.model_copy(
        update={"transport": "stdio" if old.transport == "stdio" else "auto"}
    )
    connection_changed = updated != old_normalized
    server.name = body.name
    if connection_changed:
        context = get_request_instance_runtime_context(request)
        await oauth.cancel(context.session_factory, server_id)
        server.enabled = False
        server.tools = []
    server.config = updated.model_dump_json()
    await db.commit()
    await refresh_tools(request, server_id if connection_changed else None)
    return public_server(server)


@router.post("/{server_id}/connect")
async def connect_server(server_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await get_server(db, server_id)
    context = get_request_instance_runtime_context(request)
    factory = context.session_factory

    async def complete(config, auth, store):
        tools = await client.discover(config.model_copy(update={"timeout_seconds": 300}), auth)
        async with factory() as connection_db:
            server = await get_server(connection_db, server_id)
            current = MCPConnection.model_validate_json(server.config)
            if current.url != config.url or current.command != config.command:
                raise RuntimeError("Connection changed while connecting")
            current.transport = config.transport
            server.config = current.model_dump_json()
            server.tools = tools
            server.enabled = bool(tools)
            await connection_db.commit()
        await refresh_tools(request, server_id)
        return tools

    login = await oauth.start(factory, server_id, complete)
    return login.public()


@router.get("/{server_id}/connection")
async def connection_status(server_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    server = await get_server(db, server_id)
    context = get_request_instance_runtime_context(request)
    login = oauth.get(context.session_factory, server_id)
    return (
        login.public() if login else {"status": "connected" if server.enabled else "disconnected"}
    )


@router.delete("/{server_id}/connection", status_code=204)
async def cancel_connection(server_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await get_server(db, server_id)
    context = get_request_instance_runtime_context(request)
    await oauth.cancel(context.session_factory, server_id)


@router.post("/{server_id}/test")
async def test_server(server_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    server = await get_server(db, server_id)
    try:
        tools = await client.discover(MCPConnection.model_validate_json(server.config))
    except Exception as error:
        raise HTTPException(502, connection_message(error)) from None
    server.tools = tools
    await db.commit()
    await refresh_tools(request, server_id)
    return public_server(server)


@router.patch("/{server_id}")
async def toggle_server(
    server_id: str, body: MCPServerToggle, request: Request, db: AsyncSession = Depends(get_db)
):
    server = await get_server(db, server_id)
    if body.enabled is not None:
        if body.enabled and not server.tools:
            raise HTTPException(400, "Test the connection and discover tools before enabling it")
        if not body.enabled:
            context = get_request_instance_runtime_context(request)
            await oauth.cancel(context.session_factory, server_id)
        server.enabled = body.enabled
    if body.always_load is not None:
        server.always_load = body.always_load
    await db.commit()
    await refresh_tools(request, server_id if body.enabled is not None else None)
    return public_server(server)


@router.delete("/{server_id}", status_code=204)
async def delete_server(server_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    server = await get_server(db, server_id)
    context = get_request_instance_runtime_context(request)
    await oauth.cancel(context.session_factory, server_id)
    await db.delete(server)
    await db.commit()
    await refresh_tools(request, server_id)
