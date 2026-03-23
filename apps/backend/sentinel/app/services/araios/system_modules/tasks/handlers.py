"""Native module: tasks — backed by standard module_records."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.database.database import AsyncSessionLocal
from app.models.araios import AraiosModuleRecord, araios_gen_id

_MODULE = "tasks"


def _serialize(r: AraiosModuleRecord) -> dict[str, Any]:
    return {"id": r.id, "module_name": r.module_name, **(r.data or {})}


async def handle_list(payload: dict[str, Any]) -> dict[str, Any]:
    status = payload.get("status")
    priority = payload.get("priority")
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AraiosModuleRecord)
            .where(AraiosModuleRecord.module_name == _MODULE)
            .order_by(AraiosModuleRecord.created_at.desc())
        )).scalars().all()
    tasks = [_serialize(r) for r in rows]
    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    if priority:
        tasks = [t for t in tasks if t.get("priority") == priority]
    return {"tasks": tasks}


async def handle_create(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("title"):
        raise ValueError("'title' is required")
    data = {k: v for k, v in payload.items() if k not in ("id", "session_id") and v is not None}
    async with AsyncSessionLocal() as db:
        record = AraiosModuleRecord(id=araios_gen_id(), module_name=_MODULE, data=data)
        db.add(record)
        await db.commit()
        await db.refresh(record)
    return _serialize(record)


async def handle_update(payload: dict[str, Any]) -> dict[str, Any]:
    task_id = payload.get("id")
    if not task_id:
        raise ValueError("'id' is required")
    async with AsyncSessionLocal() as db:
        record = (await db.execute(
            select(AraiosModuleRecord).where(
                AraiosModuleRecord.module_name == _MODULE,
                AraiosModuleRecord.id == task_id,
            )
        )).scalars().first()
        if not record:
            raise ValueError(f"Task '{task_id}' not found")
        updated = dict(record.data or {})
        for k, v in payload.items():
            if k not in ("id", "session_id") and v is not None:
                updated[k] = v
        record.data = updated
        await db.commit()
        await db.refresh(record)
    return _serialize(record)


async def handle_delete(payload: dict[str, Any]) -> dict[str, Any]:
    task_id = payload.get("id")
    if not task_id:
        raise ValueError("'id' is required")
    async with AsyncSessionLocal() as db:
        record = (await db.execute(
            select(AraiosModuleRecord).where(
                AraiosModuleRecord.module_name == _MODULE,
                AraiosModuleRecord.id == task_id,
            )
        )).scalars().first()
        if not record:
            raise ValueError(f"Task '{task_id}' not found")
        await db.delete(record)
        await db.commit()
    return {"ok": True, "message": f"Task '{task_id}' deleted"}
