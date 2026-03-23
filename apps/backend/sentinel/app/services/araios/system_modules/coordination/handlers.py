"""Native module: coordination — backed by standard module_records."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.database.database import AsyncSessionLocal
from app.models.araios import AraiosModuleRecord, araios_gen_id

_MODULE = "coordination"


def _serialize(r: AraiosModuleRecord) -> dict[str, Any]:
    return {"id": r.id, "module_name": r.module_name, **(r.data or {})}


async def handle_list(payload: dict[str, Any]) -> dict[str, Any]:
    agent = payload.get("agent")
    limit = payload.get("limit", 50)
    if not isinstance(limit, int) or limit < 1:
        limit = 50
    if limit > 500:
        limit = 500
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AraiosModuleRecord)
            .where(AraiosModuleRecord.module_name == _MODULE)
            .order_by(AraiosModuleRecord.created_at.asc())
            .limit(limit)
        )).scalars().all()
    messages = [_serialize(r) for r in rows]
    if agent:
        messages = [m for m in messages if m.get("agent") == agent]
    return {"messages": messages}


async def handle_send(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("agent"):
        raise ValueError("'agent' is required")
    if not payload.get("message"):
        raise ValueError("'message' is required")
    data = {k: v for k, v in payload.items() if k not in ("id", "session_id") and v is not None}
    async with AsyncSessionLocal() as db:
        record = AraiosModuleRecord(id=araios_gen_id(), module_name=_MODULE, data=data)
        db.add(record)
        await db.commit()
        await db.refresh(record)
    return _serialize(record)
