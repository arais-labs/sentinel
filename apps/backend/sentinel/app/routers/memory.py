from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.schemas.memory import (
    MemoryChildrenResponse,
    MemoryListResponse,
    MemoryResponse,
    MemorySearchRequest,
    MemoryStatsResponse,
    StoreMemoryRequest,
    UpdateMemoryRequest,
)
from app.services.memory import (
    InvalidMemoryOperationError,
    MemoryNotFoundError,
    MemoryRepository,
    MemoryService,
    MemoryServiceError,
    ParentMemoryNotFoundError,
    memory_to_response,
)

router = APIRouter()
_memory_service = MemoryService(MemoryRepository())


def _raise_http_for_memory_error(exc: MemoryServiceError) -> None:
    if isinstance(exc, MemoryNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found"
        ) from exc
    if isinstance(exc, ParentMemoryNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parent memory not found"
        ) from exc
    if isinstance(exc, InvalidMemoryOperationError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    raise exc


@router.get("/")
async def list_memory(
    request: Request,
    query: str | None = Query(default=None),
    category: str | None = Query(default=None),
    parent_id: UUID | None = Query(default=None),
    root_id: UUID | None = Query(default=None),
    roots_only: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=200),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryListResponse:
    _ = user
    memory_search = getattr(request.app.state, "memory_search_service", None)
    result = await _memory_service.list_memories(
        db,
        query=query,
        category=category,
        parent_id=parent_id,
        root_id=root_id,
        roots_only=roots_only,
        limit=limit,
        memory_search_service=memory_search,
    )
    return MemoryListResponse(
        items=[memory_to_response(item, score=result.scores.get(item.id)) for item in result.items],
        total=result.total,
    )


@router.post("/")
async def store_memory(
    payload: StoreMemoryRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryResponse:
    _ = user
    embedding_service = getattr(request.app.state, "embedding_service", None)
    try:
        memory = await _memory_service.create_memory(
            db,
            content=payload.content,
            title=payload.title,
            summary=payload.summary,
            category=payload.category,
            parent_id=payload.parent_id,
            importance=payload.importance,
            pinned=payload.pinned,
            metadata=payload.metadata,
            embedding=payload.embedding,
            embedding_service=embedding_service,
            ignore_embedding_errors=True,
        )
    except MemoryServiceError as exc:
        _raise_http_for_memory_error(exc)
        raise
    return memory_to_response(memory)


@router.post("/search")
async def search_memory(
    payload: MemorySearchRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryListResponse:
    _ = user
    memory_search = getattr(request.app.state, "memory_search_service", None)
    result = await _memory_service.search_memories(
        db,
        query=payload.query,
        category=payload.category,
        root_id=payload.root_id,
        limit=payload.limit,
        memory_search_service=memory_search,
    )
    return MemoryListResponse(
        items=[memory_to_response(item, score=result.scores.get(item.id)) for item in result.items],
        total=result.total,
    )


@router.get("/roots")
async def list_root_memories(
    category: str | None = Query(default=None),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryListResponse:
    _ = user
    roots = await _memory_service.list_root_memories(db, category=category)
    return MemoryListResponse(items=[memory_to_response(item) for item in roots], total=len(roots))


@router.get("/nodes/{id}")
async def get_memory_node(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryResponse:
    _ = user
    try:
        memory = await _memory_service.get_memory(db, id)
    except MemoryServiceError as exc:
        _raise_http_for_memory_error(exc)
        raise
    return memory_to_response(memory)


@router.get("/nodes/{id}/children")
async def list_memory_children(
    id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryChildrenResponse:
    _ = user
    try:
        result = await _memory_service.list_children(db, parent_id=id, limit=limit)
    except MemoryServiceError as exc:
        _raise_http_for_memory_error(exc)
        raise
    return MemoryChildrenResponse(
        parent_id=result.parent_id,
        items=[memory_to_response(item) for item in result.items],
        total=result.total,
    )


@router.patch("/nodes/{id}")
async def update_memory_node(
    id: UUID,
    payload: UpdateMemoryRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryResponse:
    _ = user
    embedding_service = getattr(request.app.state, "embedding_service", None)
    try:
        memory = await _memory_service.update_memory(
            db,
            memory_id=id,
            updates=payload.model_dump(exclude_unset=True),
            embedding_service=embedding_service,
            ignore_embedding_errors=True,
        )
    except MemoryServiceError as exc:
        _raise_http_for_memory_error(exc)
        raise
    return memory_to_response(memory)


@router.post("/nodes/{id}/touch")
async def touch_memory_node(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryResponse:
    _ = user
    try:
        memory = await _memory_service.touch_memory(db, id)
    except MemoryServiceError as exc:
        _raise_http_for_memory_error(exc)
        raise
    return memory_to_response(memory)


@router.get("/stats")
async def memory_stats(
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryStatsResponse:
    _ = user
    categories = await _memory_service.memory_stats(db)
    return MemoryStatsResponse(total_memories=sum(categories.values()), categories=categories)


@router.delete("/{id}")
async def delete_memory(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    _ = user
    try:
        await _memory_service.delete_memory(db, id)
    except MemoryServiceError as exc:
        _raise_http_for_memory_error(exc)
        raise
    return {"status": "deleted"}
