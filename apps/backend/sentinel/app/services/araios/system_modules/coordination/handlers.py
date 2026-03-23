"""Native module: coordination — inter-agent coordination messaging."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.database.database import AsyncSessionLocal
from app.models.araios import AraiosCoordinationMessage, araios_gen_id
from app.services.tools.executor import ToolValidationError

logger = logging.getLogger(__name__)
ALLOWED_COORDINATION_COMMANDS = ("list", "send")


# ── Helpers ──


def _msg_to_dict(m: AraiosCoordinationMessage) -> dict[str, Any]:
    """Serialize a coordination message model to a dict."""
    return {
        "id": m.id,
        "agent": m.agent,
        "message": m.message,
        "context": m.context,
        "createdAt": m.created_at.isoformat() if m.created_at else None,
    }


# ---------------------------------------------------------------------------
# Handler functions (module-level)
# ---------------------------------------------------------------------------


async def handle_list(payload: dict[str, Any]) -> dict[str, Any]:
    agent = payload.get("agent")
    limit = payload.get("limit", 50)
    if not isinstance(limit, int) or limit < 1:
        limit = 50
    if limit > 500:
        limit = 500
    async with AsyncSessionLocal() as db:
        stmt = select(AraiosCoordinationMessage).order_by(
            AraiosCoordinationMessage.seq.asc()
        )
        if agent:
            stmt = stmt.where(AraiosCoordinationMessage.agent == agent)
        stmt = stmt.limit(limit)
        result = await db.execute(stmt)
        rows = result.scalars().all()
        return {"messages": [_msg_to_dict(r) for r in rows]}


async def handle_send(payload: dict[str, Any]) -> dict[str, Any]:
    agent = payload.get("agent")
    message = payload.get("message")
    context = payload.get("context")
    if not agent:
        raise ValueError("'agent' is required")
    if not message:
        raise ValueError("'message' is required")
    async with AsyncSessionLocal() as db:
        msg = AraiosCoordinationMessage(
            id=araios_gen_id(),
            agent=agent,
            message=message,
            context=context,
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        return _msg_to_dict(msg)


# ---------------------------------------------------------------------------
# Unified tool dispatch
# ---------------------------------------------------------------------------

def _coordination_command(payload: dict[str, Any]) -> str:
    raw = payload.get("command")
    if not isinstance(raw, str) or not raw.strip():
        raise ToolValidationError("Field 'command' must be a non-empty string")
    normalized = raw.strip().lower()
    if normalized not in ALLOWED_COORDINATION_COMMANDS:
        raise ToolValidationError(
            "Field 'command' must be one of: " + ", ".join(ALLOWED_COORDINATION_COMMANDS)
        )
    return normalized


async def handle_run(payload: dict[str, Any]) -> dict[str, Any]:
    command = _coordination_command(payload)
    if command == "list":
        return await handle_list(payload)
    return await handle_send(payload)
