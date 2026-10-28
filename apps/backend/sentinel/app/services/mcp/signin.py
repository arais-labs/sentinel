"""MCP calls that need the user to sign in wait for it, instead of failing the agent's call.

A server whose saved sign-in no longer works (the provider expired it) is marked as needing sign-in,
which Settings shows; the user is notified; the pending call waits until a sign-in for that server
completes, then runs once more. Without one before the deadline, the call fails with a clear reason.
"""

import asyncio
import logging
import weakref
from contextlib import suppress

from app.models.mcp import MCPServer
from app.schemas.mcp import MCPConnection

logger = logging.getLogger(__name__)

WAIT_SECONDS = 600
# Kept only while a call waits on it: a wait that timed out leaves nothing behind.
_completed: weakref.WeakValueDictionary[tuple[object, str], asyncio.Event] = (
    weakref.WeakValueDictionary()
)


async def _set_flag(factory, server_id: str, required: bool) -> None:
    async with factory() as db:
        server = await db.get(MCPServer, server_id)
        if server is None:
            return
        config = MCPConnection.model_validate_json(server.config)
        if bool(config.oauth.get("sign_in_required")) == required:
            return
        if required:
            config.oauth["sign_in_required"] = True
        else:
            config.oauth.pop("sign_in_required", None)
        server.config = config.model_dump_json()
        await db.commit()


async def wait(factory, server_id: str, name: str, *, instance_name=None, session_id=None) -> bool:
    """Marks the server as needing sign-in, tells the user, and waits for a sign-in to complete.
    True once it has; False after WAIT_SECONDS."""
    event = _completed.setdefault((factory, server_id), asyncio.Event())
    await _set_flag(factory, server_id, True)
    await _notify(server_id, name, instance_name=instance_name, session_id=session_id)
    try:
        async with asyncio.timeout(WAIT_SECONDS):
            await event.wait()
        return True
    except TimeoutError:
        return False


async def completed(factory, server_id: str) -> None:
    """A sign-in for the server finished: clear the mark and wake every waiting call."""
    await _set_flag(factory, server_id, False)
    event = _completed.pop((factory, server_id), None)
    if event is not None:
        event.set()


async def _notify(server_id: str, name: str, *, instance_name=None, session_id=None) -> None:
    from app.services.notifications import Notification, publish_notification

    target = {"mcpServerId": server_id}
    if instance_name:
        target["instanceName"] = instance_name
    if session_id:
        target["sessionId"] = str(session_id)
    # One notification per server while sign-in is pending (the key replaces, never stacks).
    with suppress(Exception):
        await publish_notification(
            Notification(
                source="MCP",
                key=f"mcp-sign-in:{server_id}",
                title=f"Sign in to {name}"[:160],
                message=(
                    f"{name} needs you to sign in again. An agent's call is waiting for it and "
                    f"continues once you have (for up to {WAIT_SECONDS // 60} minutes)."
                ),
                severity="warning",
                target=target,
            )
        )
