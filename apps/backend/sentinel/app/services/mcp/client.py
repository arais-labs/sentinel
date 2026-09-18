"""MCP SDK transport boundary. Calls are never automatically replayed."""

from contextlib import AsyncExitStack, asynccontextmanager
import asyncio
import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from app.schemas.mcp import MCPConnection
from app.services.mcp.errors import http_status


def _contains_mcp_error(error: BaseException) -> bool:
    if isinstance(error, MCPError):
        return True
    return any(_contains_mcp_error(child) for child in getattr(error, "exceptions", [])) or (
        error.__cause__ is not None and _contains_mcp_error(error.__cause__)
    )


@asynccontextmanager
async def _transport(config: MCPConnection, auth=None):
    # Keep SDK task groups and their cleanup in the same task.
    async with AsyncExitStack() as stack:
        if config.transport == "stdio":
            transport = StdioServerParameters(
                command=config.command, args=config.args, env=config.env
            )
        elif config.transport == "sse":
            transport = sse_client(
                config.url,
                headers=config.headers,
                sse_read_timeout=config.timeout_seconds,
                auth=auth,
            )
        else:
            http = await stack.enter_async_context(
                httpx2.AsyncClient(
                    headers=config.headers, timeout=config.timeout_seconds, auth=auth
                )
            )
            transport = streamable_http_client(config.url, http_client=http)
        async with Client(
            transport, read_timeout_seconds=config.timeout_seconds, cache=None
        ) as session:
            yield session


@asynccontextmanager
async def connect(config: MCPConnection, auth=None):
    # Fallback applies only to connection establishment, never a tools/call.
    async with AsyncExitStack() as stack:
        try:
            session = await stack.enter_async_context(_transport(config, auth))
        except Exception as error:
            # Legacy SSE servers commonly answer the Streamable HTTP probe with
            # 405. The SDK may preserve that HTTP status or translate it into a
            # generic MCP negotiation error, depending on the response body.
            fallback_error = http_status(error) in {400, 404, 405} or _contains_mcp_error(error)
            if config.transport != "auto" or not fallback_error:
                raise
            legacy = config.model_copy(update={"transport": "sse"})
            session = await stack.enter_async_context(_transport(legacy, auth))
        yield session


async def discover(config: MCPConnection, auth=None) -> list[dict]:
    async with asyncio.timeout(config.timeout_seconds):
        return await _discover(config, auth)


async def _discover(config: MCPConnection, auth=None) -> list[dict]:
    tools = []
    cursors = set()
    async with connect(config, auth) as session:
        cursor = None
        while True:
            page = await session.list_tools(cursor=cursor)
            tools.extend(tool.model_dump(mode="json", by_alias=True) for tool in page.tools)
            if len(tools) > 1000:
                raise ValueError("Oversized MCP tool catalog")
            cursor = page.next_cursor
            if not cursor:
                return tools
            if cursor in cursors or len(cursors) >= 1000:
                raise ValueError("Invalid or oversized MCP tool catalog")
            cursors.add(cursor)


async def call(config: MCPConnection, name: str, arguments: dict) -> dict:
    async with connect(config) as session:
        result = await session.call_tool(name, arguments)
        return serialize_result(result)


def serialize_result(result) -> dict:
    payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    for block in payload.get("content", []):
        if block.get("type") == "image" and "data" in block:
            block["image_base64"] = block.pop("data")
        elif block.get("type") == "audio" and "data" in block:
            block["data"] = "[Audio content is not supported by this integration]"
        elif block.get("type") == "resource" and "blob" in block.get("resource", {}):
            block["resource"]["blob"] = "[Binary resource omitted]"
    return payload
