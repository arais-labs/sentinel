"""AraiOS Documents router — async SQLAlchemy."""
from __future__ import annotations

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, Query, Header

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.models.araios import AraiosDocument, AraiosPermission, AraiosApproval, araios_gen_id
from app.schemas.araios import (
    DocumentCreate,
    DocumentUpdate,
    DocumentOut,
    DocumentListItem,
    DocumentListResponse,
    OkResponse,
)


router = APIRouter(tags=["araios-documents"])


# ── Helpers ──


def _require_araios_permission(action: str):
    async def _check(
        user: TokenPayload = Depends(require_auth),
        db: AsyncSession = Depends(get_db),
    ):
        if user.role == "admin":
            return
        result = await db.execute(select(AraiosPermission).where(AraiosPermission.action == action))
        perm = result.scalars().first()
        level = perm.level if perm else "deny"
        if level == "allow":
            return
        if level == "deny":
            raise HTTPException(status_code=403, detail=f"Action '{action}' is not allowed for agent role")
        if level == "approval":
            approval = AraiosApproval(
                id=araios_gen_id(),
                status="pending",
                action=action,
                description=f"Agent requested: {action}",
            )
            db.add(approval)
            await db.commit()
            await db.refresh(approval)
            raise HTTPException(
                status_code=202,
                detail={
                    "message": "Action requires approval",
                    "approval": {"id": approval.id, "status": approval.status, "action": approval.action},
                },
            )

    return _check


def _get_agent_id(user: TokenPayload = Depends(require_auth)) -> str:
    return user.agent_id or user.sub


def _doc_to_out(d: AraiosDocument) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        slug=d.slug,
        title=d.title,
        content=d.content,
        author=d.author,
        lastEditedBy=d.last_edited_by,
        tags=d.tags,
        version=d.version,
        createdAt=d.created_at.isoformat() if d.created_at else None,
        updatedAt=d.updated_at.isoformat() if d.updated_at else None,
    )


def _doc_to_list_item(d: AraiosDocument) -> DocumentListItem:
    return DocumentListItem(
        id=d.id,
        slug=d.slug,
        title=d.title,
        author=d.author,
        lastEditedBy=d.last_edited_by,
        tags=d.tags,
        version=d.version,
        createdAt=d.created_at.isoformat() if d.created_at else None,
        updatedAt=d.updated_at.isoformat() if d.updated_at else None,
    )


# ── Routes ──


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    tag: str | None = Query(None),
    _user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AraiosDocument)
    if tag:
        stmt = stmt.where(AraiosDocument.tags.op("@>")([tag]))
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return DocumentListResponse(documents=[_doc_to_list_item(r) for r in rows])


@router.get("/{slug}", response_model=DocumentOut)
async def get_document(
    slug: str,
    _user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AraiosDocument).where(AraiosDocument.slug == slug))
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return _doc_to_out(doc)


@router.post("", response_model=DocumentOut, status_code=201)
async def create_document(
    body: DocumentCreate,
    agent_id: str = Depends(_get_agent_id),
    _perm: None = Depends(_require_araios_permission("documents.create")),
    db: AsyncSession = Depends(get_db),
):
    # Check slug uniqueness
    result = await db.execute(select(AraiosDocument).where(AraiosDocument.slug == body.slug))
    if result.scalars().first():
        raise HTTPException(status_code=409, detail="Document with this slug already exists")

    doc = AraiosDocument(
        id=araios_gen_id(),
        slug=body.slug,
        title=body.title,
        content=body.content,
        author=agent_id,
        last_edited_by=agent_id,
        tags=body.tags,
        version=1,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return _doc_to_out(doc)


@router.put("/{slug}", response_model=DocumentOut)
async def update_document(
    slug: str,
    body: DocumentUpdate,
    agent_id: str = Depends(_get_agent_id),
    _perm: None = Depends(_require_araios_permission("documents.update")),
    db: AsyncSession = Depends(get_db),
    if_match: str | None = Header(None, alias="If-Match"),
):
    result = await db.execute(select(AraiosDocument).where(AraiosDocument.slug == slug))
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Optimistic locking via If-Match header
    if if_match is not None:
        expected_version = int(if_match)
        if doc.version != expected_version:
            raise HTTPException(
                status_code=412,
                detail=f"Version conflict: expected {expected_version}, current {doc.version}",
            )

    if body.title is not None:
        doc.title = body.title
    doc.content = body.content
    if body.tags is not None:
        doc.tags = body.tags
    doc.last_edited_by = agent_id
    doc.version += 1

    await db.commit()
    await db.refresh(doc)
    return _doc_to_out(doc)


@router.delete("/{slug}", response_model=OkResponse)
async def delete_document(
    slug: str,
    _perm: None = Depends(_require_araios_permission("documents.delete")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AraiosDocument).where(AraiosDocument.slug == slug))
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    await db.delete(doc)
    await db.commit()
    return OkResponse()
