"""Native module: documents — backed by standard module_records."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.database.database import AsyncSessionLocal
from app.models.araios import AraiosModuleRecord, araios_gen_id

_MODULE = "documents"


def _serialize(r: AraiosModuleRecord) -> dict[str, Any]:
    return {"id": r.id, "module_name": r.module_name, **(r.data or {})}


async def handle_list(payload: dict[str, Any]) -> dict[str, Any]:
    tag = payload.get("tag")
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AraiosModuleRecord)
            .where(AraiosModuleRecord.module_name == _MODULE)
            .order_by(AraiosModuleRecord.created_at.desc())
        )).scalars().all()
    docs = [_serialize(r) for r in rows]
    if tag:
        docs = [d for d in docs if tag in (d.get("tags") or [])]
    return {"documents": docs}


async def handle_get(payload: dict[str, Any]) -> dict[str, Any]:
    doc_id = payload.get("id")
    slug = payload.get("slug")
    if not doc_id and not slug:
        raise ValueError("'id' or 'slug' is required")
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AraiosModuleRecord).where(AraiosModuleRecord.module_name == _MODULE)
        )).scalars().all()
    for r in rows:
        data = r.data or {}
        if doc_id and r.id == doc_id:
            return _serialize(r)
        if slug and data.get("slug") == slug:
            return _serialize(r)
    raise ValueError("Document not found")


async def handle_create(payload: dict[str, Any]) -> dict[str, Any]:
    for field in ("title", "slug", "author"):
        if not payload.get(field):
            raise ValueError(f"'{field}' is required")
    slug = payload["slug"]
    # Check slug uniqueness
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AraiosModuleRecord).where(AraiosModuleRecord.module_name == _MODULE)
        )).scalars().all()
        if any((r.data or {}).get("slug") == slug for r in rows):
            raise ValueError(f"Document with slug '{slug}' already exists")
        data = {k: v for k, v in payload.items() if k not in ("id", "session_id") and v is not None}
        data.setdefault("content", "")
        data.setdefault("version", 1)
        record = AraiosModuleRecord(id=araios_gen_id(), module_name=_MODULE, data=data)
        db.add(record)
        await db.commit()
        await db.refresh(record)
    return _serialize(record)


async def handle_update(payload: dict[str, Any]) -> dict[str, Any]:
    doc_id = payload.get("id")
    if not doc_id:
        raise ValueError("'id' is required")
    async with AsyncSessionLocal() as db:
        record = (await db.execute(
            select(AraiosModuleRecord).where(
                AraiosModuleRecord.module_name == _MODULE,
                AraiosModuleRecord.id == doc_id,
            )
        )).scalars().first()
        if not record:
            raise ValueError(f"Document '{doc_id}' not found")
        updated = dict(record.data or {})
        for k, v in payload.items():
            if k not in ("id", "session_id") and v is not None:
                updated[k] = v
        updated["version"] = updated.get("version", 1) + 1
        record.data = updated
        await db.commit()
        await db.refresh(record)
    return _serialize(record)


async def handle_delete(payload: dict[str, Any]) -> dict[str, Any]:
    doc_id = payload.get("id")
    if not doc_id:
        raise ValueError("'id' is required")
    async with AsyncSessionLocal() as db:
        record = (await db.execute(
            select(AraiosModuleRecord).where(
                AraiosModuleRecord.module_name == _MODULE,
                AraiosModuleRecord.id == doc_id,
            )
        )).scalars().first()
        if not record:
            raise ValueError(f"Document '{doc_id}' not found")
        await db.delete(record)
        await db.commit()
    return {"ok": True, "message": f"Document '{doc_id}' deleted"}
