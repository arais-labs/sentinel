"""Per-session loading of on-demand MCP servers.

The registry keeps every MCP tool; a session only shows the model the servers it has
loaded. State lives in the database and is mirrored in memory so the tool list can be
filtered synchronously on every LLM iteration and a load takes effect mid-turn.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp import MCPServer, MCPSessionExposure
from app.models.sessions import Message

# A loaded server drops out of the prompt after this many assistant messages without a call.
EXPIRY_MESSAGES = 6
# Rows of sessions that never come back are swept after this long.
STALE_AFTER = timedelta(days=7)


@dataclass
class SessionExposure:
    session_id: UUID
    clock: int = 0
    enabled: dict[str, MCPServer] = field(default_factory=dict)
    pinned: set[str] = field(default_factory=set)
    loaded: set[str] = field(default_factory=set)

    @property
    def exposed(self) -> set[str]:
        return self.pinned | self.loaded

    @property
    def has_servers(self) -> bool:
        return bool(self.enabled)


_states: dict[UUID, SessionExposure] = {}


def state_for(session_id: UUID) -> SessionExposure:
    state = _states.get(session_id)
    if state is None:
        state = _states[session_id] = SessionExposure(session_id=session_id)
    return state


def forget(session_id: UUID) -> None:
    _states.pop(session_id, None)


async def _clock(db: AsyncSession, session_id: UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.session_id == session_id, Message.role == "assistant")
        )
        or 0
    )


async def refresh(db: AsyncSession, session_id: UUID) -> SessionExposure:
    """Rebuild the in-memory view at turn start, expiring idle servers."""
    state = state_for(session_id)
    state.clock = await _clock(db, session_id)
    await db.execute(
        delete(MCPSessionExposure).where(
            MCPSessionExposure.updated_at < datetime.now(UTC) - STALE_AFTER
        )
    )
    servers = (
        (await db.execute(select(MCPServer).where(MCPServer.enabled.is_(True)))).scalars().all()
    )
    state.enabled = {server.id: server for server in servers}
    state.pinned = {server.id for server in servers if server.always_load}
    rows = (
        (
            await db.execute(
                select(MCPSessionExposure).where(MCPSessionExposure.session_id == session_id)
            )
        )
        .scalars()
        .all()
    )
    expired = [
        row
        for row in rows
        if row.server_id not in state.enabled
        or row.server_id in state.pinned
        or state.clock - row.last_used_at > EXPIRY_MESSAGES
    ]
    for row in expired:
        await db.delete(row)
    await db.commit()
    state.loaded = {row.server_id for row in rows if row not in expired}
    return state


async def load(db: AsyncSession, session_id: UUID, server_id: str) -> SessionExposure:
    state = state_for(session_id)
    clock = await _clock(db, session_id)
    row = await db.get(MCPSessionExposure, (session_id, server_id))
    if row is None:
        db.add(
            MCPSessionExposure(
                session_id=session_id, server_id=server_id, loaded_at=clock, last_used_at=clock
            )
        )
    else:
        row.last_used_at = clock
        row.updated_at = datetime.now(UTC)
    await db.commit()
    state.loaded.add(server_id)
    return state


async def touch(db: AsyncSession, session_id: UUID, server_id: str) -> None:
    row = await db.get(MCPSessionExposure, (session_id, server_id))
    if row is None:
        return
    row.last_used_at = await _clock(db, session_id)
    row.updated_at = datetime.now(UTC)
    await db.commit()


async def clear(db: AsyncSession, session_id: UUID) -> None:
    """Compaction resets what the model has in view; loaded servers go with it."""
    rows = (
        (
            await db.execute(
                select(MCPSessionExposure).where(MCPSessionExposure.session_id == session_id)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        await db.delete(row)
    await db.commit()
    state = _states.get(session_id)
    if state is not None:
        state.loaded.clear()


def summary_block(state: SessionExposure) -> str | None:
    """System-prompt line per enabled server so the model knows what it can load."""
    if not state.enabled:
        return None
    lines = [
        "MCP servers available in this workspace. Their tools are not in your tool list until "
        "loaded: call catalog_load with the server id, then use the tools directly. "
        "Pinned servers are already loaded."
    ]
    for server in sorted(state.enabled.values(), key=lambda item: item.name.lower()):
        status = " [loaded]" if server.id in state.exposed else ""
        lines.append(f"- {server.id}: {server.name} ({len(server.tools)} tools){status}")
    return "\n".join(lines)
