"""Session-isolated MCP connections owned and cleaned up by a single task."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from app.schemas.mcp import MCPConnection
from app.services.mcp import oauth
from app.services.mcp.client import connect, serialize_result

IDLE_SECONDS = 900


@dataclass
class Request:
    name: str
    arguments: dict
    result: asyncio.Future


class Connection:
    def __init__(self, config: MCPConnection, auth=None):
        self.auth = auth
        self.config = config
        self.queue: asyncio.Queue[Request] = asyncio.Queue()
        self.task = asyncio.create_task(self.run())

    async def run(self):
        current = None
        try:
            async with connect(self.config, self.auth) as session:
                while True:
                    try:
                        current = await asyncio.wait_for(self.queue.get(), IDLE_SECONDS)
                    except TimeoutError:
                        return
                    if current.result.cancelled():
                        current = None
                        continue
                    async with asyncio.timeout(self.config.timeout_seconds):
                        result = await session.call_tool(current.name, current.arguments)
                    if not current.result.done():
                        current.result.set_result(serialize_result(result))
                    current = None
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never return credentials from transport errors or replay a call.
            pass
        finally:
            pending = [current] if current else []
            while not self.queue.empty():
                pending.append(self.queue.get_nowait())
            for request in pending:
                if not request.result.done():
                    request.result.set_exception(
                        RuntimeError("MCP connection closed; the call was not retried")
                    )

    async def call(self, name, arguments):
        future = asyncio.get_running_loop().create_future()
        self.queue.put_nowait(Request(name, arguments, future))
        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                return await future
        except (asyncio.CancelledError, TimeoutError):
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            raise


_connections: dict[tuple[Any, str, str], Connection] = {}


async def call(owner, server_id, session_id, config, name, arguments):
    key = (owner, server_id, str(session_id))
    connection = _connections.get(key)
    if connection is None or connection.task.done():
        auth = None
        if config.oauth and not any(key.lower() == "authorization" for key in config.headers):
            auth = oauth.provider(config, oauth.TokenStore(owner, server_id, config.url))
        connection = Connection(config, auth)
        _connections[key] = connection

        def remove_finished(task):
            if _connections.get(key) is connection:
                _connections.pop(key, None)

        connection.task.add_done_callback(remove_finished)
    return await connection.call(name, arguments)


async def close(owner, server_id=None):
    selected = [
        (key, value)
        for key, value in _connections.items()
        if key[0] is owner and (server_id is None or key[1] == server_id)
    ]
    for key, connection in selected:
        _connections.pop(key, None)
        connection.task.cancel()
    await asyncio.gather(*(connection.task for _, connection in selected), return_exceptions=True)
