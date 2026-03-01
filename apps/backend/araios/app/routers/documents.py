from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.dependencies import get_db
from app.middleware.auth import require_permission, get_agent_id
from app.database.models import Document, gen_id
from app.schemas import (
    DocumentCreate, DocumentUpdate,
    DocumentOut, DocumentListItem, DocumentListResponse, OkResponse,
)

router = APIRouter()

_FIELD_MAP = {
    "lastEditedBy": "last_edited_by",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
}

_REVERSE_MAP = {v: k for k, v in _FIELD_MAP.items()}


def _to_dict(obj: Document, include_content: bool = True) -> dict:
    d = {}
    for col in Document.__table__.columns:
        if not include_content and col.name == "content":
            continue
        val = getattr(obj, col.name)
        key = _REVERSE_MAP.get(col.name, col.name)
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        d[key] = val
    return d


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List all documents",
    description="Returns all documents (without content). Optional tag filter.",
)
async def list_documents(
    tag: Optional[str] = Query(None, description="Filter by tag"),
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("documents.list")),
):
    q = db.query(Document).order_by(Document.updated_at.desc())
    docs = q.all()
    if tag:
        docs = [d for d in docs if d.tags and tag in d.tags]
    return {"documents": [_to_dict(d, include_content=False) for d in docs]}


@router.get(
    "/{slug}",
    response_model=DocumentOut,
    summary="Get document by slug",
    description="Returns full document including content.",
)
async def get_document(
    slug: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("documents.list")),
):
    doc = db.query(Document).filter(Document.slug == slug).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return _to_dict(doc)


@router.post(
    "",
    status_code=201,
    response_model=DocumentOut,
    summary="Create a document",
    description="Create a new document. Author auto-set from token. 409 if slug exists.",
)
async def create_document(
    body: DocumentCreate,
    db: Session = Depends(get_db),
    agent_id: str = Depends(get_agent_id),
    _: None = Depends(require_permission("documents.create")),
):
    existing = db.query(Document).filter(Document.slug == body.slug).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Document with slug '{body.slug}' already exists")

    doc = Document(
        id=gen_id(),
        slug=body.slug,
        title=body.title,
        content=body.content,
        author=agent_id,
        last_edited_by=agent_id,
        tags=body.tags or [],
        version=1,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return _to_dict(doc)


@router.put(
    "/{slug}",
    response_model=DocumentOut,
    summary="Update a document",
    description="Full replace of content/title/tags. Version increments. "
    "Optional If-Match header for optimistic locking.",
)
async def update_document(
    slug: str,
    body: DocumentUpdate,
    db: Session = Depends(get_db),
    agent_id: str = Depends(get_agent_id),
    _: None = Depends(require_permission("documents.update")),
    if_match: Optional[str] = Header(None, alias="If-Match"),
):
    doc = db.query(Document).filter(Document.slug == slug).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Optimistic locking
    if if_match is not None:
        try:
            expected_version = int(if_match)
        except ValueError:
            raise HTTPException(status_code=400, detail="If-Match must be an integer version")
        if doc.version != expected_version:
            raise HTTPException(
                status_code=409,
                detail=f"Version conflict: expected {expected_version}, current is {doc.version}",
            )

    doc.content = body.content
    if body.title is not None:
        doc.title = body.title
    if body.tags is not None:
        doc.tags = body.tags
    doc.last_edited_by = agent_id
    doc.version += 1
    db.commit()
    db.refresh(doc)
    return _to_dict(doc)


@router.delete(
    "/{slug}",
    status_code=204,
    summary="Delete a document",
    description="Delete a document by slug.",
)
async def delete_document(
    slug: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("documents.delete")),
):
    count = db.query(Document).filter(Document.slug == slug).delete()
    db.commit()
    if not count:
        raise HTTPException(status_code=404, detail="Document not found")
    return None
