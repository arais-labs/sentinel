"""Native module: documents — markdown document management."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.database.database import AsyncSessionLocal
from app.models.araios import AraiosDocument, araios_gen_id

logger = logging.getLogger(__name__)


# ── Helpers ──


def _doc_to_dict(d: AraiosDocument) -> dict[str, Any]:
    """Serialize a document model to a dict."""
    return {
        "id": d.id,
        "slug": d.slug,
        "title": d.title,
        "content": d.content,
        "author": d.author,
        "lastEditedBy": d.last_edited_by,
        "tags": d.tags,
        "version": d.version,
        "createdAt": d.created_at.isoformat() if d.created_at else None,
        "updatedAt": d.updated_at.isoformat() if d.updated_at else None,
    }


def _doc_to_list_item(d: AraiosDocument) -> dict[str, Any]:
    """Serialize a document model to a list-item dict (no content)."""
    return {
        "id": d.id,
        "slug": d.slug,
        "title": d.title,
        "author": d.author,
        "lastEditedBy": d.last_edited_by,
        "tags": d.tags,
        "version": d.version,
        "createdAt": d.created_at.isoformat() if d.created_at else None,
        "updatedAt": d.updated_at.isoformat() if d.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Handler functions (module-level)
# ---------------------------------------------------------------------------


async def handle_list(payload: dict[str, Any]) -> dict[str, Any]:
    tag = payload.get("tag")
    async with AsyncSessionLocal() as db:
        stmt = select(AraiosDocument)
        if tag:
            stmt = stmt.where(AraiosDocument.tags.op("@>")([tag]))
        result = await db.execute(stmt)
        rows = result.scalars().all()
        return {"documents": [_doc_to_list_item(r) for r in rows]}


async def handle_get(payload: dict[str, Any]) -> dict[str, Any]:
    doc_id = payload.get("id")
    slug = payload.get("slug")
    if not doc_id and not slug:
        raise ValueError("'id' or 'slug' is required")
    async with AsyncSessionLocal() as db:
        if slug:
            result = await db.execute(
                select(AraiosDocument).where(AraiosDocument.slug == slug)
            )
        else:
            result = await db.execute(
                select(AraiosDocument).where(AraiosDocument.id == doc_id)
            )
        doc = result.scalars().first()
        if not doc:
            raise ValueError("Document not found")
        return _doc_to_dict(doc)


async def handle_create(payload: dict[str, Any]) -> dict[str, Any]:
    title = payload.get("title")
    slug = payload.get("slug")
    author = payload.get("author")
    if not title:
        raise ValueError("'title' is required")
    if not slug:
        raise ValueError("'slug' is required")
    if not author:
        raise ValueError("'author' is required")
    async with AsyncSessionLocal() as db:
        # Check slug uniqueness
        result = await db.execute(
            select(AraiosDocument).where(AraiosDocument.slug == slug)
        )
        if result.scalars().first():
            raise ValueError(f"Document with slug '{slug}' already exists")
        doc = AraiosDocument(
            id=araios_gen_id(),
            slug=slug,
            title=title,
            content=payload.get("content", ""),
            author=author,
            last_edited_by=author,
            tags=payload.get("tags"),
            version=1,
        )
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
        return _doc_to_dict(doc)


async def handle_update(payload: dict[str, Any]) -> dict[str, Any]:
    doc_id = payload.get("id")
    if not doc_id:
        raise ValueError("'id' is required")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AraiosDocument).where(AraiosDocument.id == doc_id)
        )
        doc = result.scalars().first()
        if not doc:
            raise ValueError(f"Document '{doc_id}' not found")
        if payload.get("title") is not None:
            doc.title = payload["title"]
        if payload.get("content") is not None:
            doc.content = payload["content"]
        if payload.get("tags") is not None:
            doc.tags = payload["tags"]
        if payload.get("author") is not None:
            doc.last_edited_by = payload["author"]
        doc.version += 1
        await db.commit()
        await db.refresh(doc)
        return _doc_to_dict(doc)


async def handle_delete(payload: dict[str, Any]) -> dict[str, Any]:
    doc_id = payload.get("id")
    if not doc_id:
        raise ValueError("'id' is required")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AraiosDocument).where(AraiosDocument.id == doc_id)
        )
        doc = result.scalars().first()
        if not doc:
            raise ValueError(f"Document '{doc_id}' not found")
        await db.delete(doc)
        await db.commit()
        return {"ok": True, "message": f"Document '{doc_id}' deleted"}
