from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.models import Memory
from app.schemas.memory import (
    MemoryChildrenResponse,
    MemoryListResponse,
    MemoryResponse,
    MemorySearchRequest,
    MemoryStatsResponse,
    StoreMemoryRequest,
    UpdateMemoryRequest,
)

router = APIRouter()
_MIN_TIME = datetime.min.replace(tzinfo=UTC)


@router.get("")
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
    if query and memory_search is not None:
        results = await memory_search.search(db, query, category=category, limit=limit)
        items = [item.memory for item in results]
        if root_id is not None:
            items = _filter_by_root(items, await _all_memories(db), root_id)
        if parent_id is not None:
            items = [item for item in items if item.parent_id == parent_id]
        if roots_only:
            items = [item for item in items if item.parent_id is None]
        return MemoryListResponse(
            items=[_memory_response(item, score=_score_for(item.id, results)) for item in items],
            total=len(items),
        )

    memories = await _all_memories(db)
    memories = _apply_filters(
        memories,
        query=query,
        category=category,
        parent_id=parent_id,
        root_id=root_id,
        roots_only=roots_only,
    )
    memories.sort(key=lambda m: m.created_at or _MIN_TIME, reverse=True)
    items = memories[:limit]
    return MemoryListResponse(items=[_memory_response(item) for item in items], total=len(memories))


@router.post("")
async def store_memory(
    payload: StoreMemoryRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryResponse:
    _ = user
    if payload.parent_id is not None:
        parent = await _get_memory(db, payload.parent_id)
        if parent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent memory not found")

    embedding = payload.embedding
    if embedding is None:
        embedding_service = getattr(request.app.state, "embedding_service", None)
        if embedding_service is not None:
            try:
                embedding = await embedding_service.embed(payload.content)
            except Exception:  # noqa: BLE001
                embedding = None

    memory = Memory(
        content=payload.content,
        title=payload.title,
        summary=payload.summary,
        category=payload.category,
        parent_id=payload.parent_id,
        importance=payload.importance,
        pinned=payload.pinned,
        metadata_json=payload.metadata,
        embedding=embedding,
    )
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return _memory_response(memory)


@router.post("/search")
async def search_memory(
    payload: MemorySearchRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryListResponse:
    _ = user
    memory_search = getattr(request.app.state, "memory_search_service", None)
    if memory_search is not None:
        results = await memory_search.search(
            db,
            payload.query,
            category=payload.category,
            limit=payload.limit,
        )
        items = [item.memory for item in results]
        if payload.root_id is not None:
            items = _filter_by_root(items, await _all_memories(db), payload.root_id)
        return MemoryListResponse(
            items=[_memory_response(item, score=_score_for(item.id, results)) for item in items],
            total=len(items),
        )

    memories = await _all_memories(db)
    memories = _apply_filters(
        memories,
        query=payload.query,
        category=payload.category,
        root_id=payload.root_id,
    )
    memories.sort(key=lambda m: m.created_at or _MIN_TIME, reverse=True)
    trimmed = memories[: payload.limit]
    return MemoryListResponse(items=[_memory_response(item) for item in trimmed], total=len(memories))


@router.get("/roots")
async def list_root_memories(
    category: str | None = Query(default=None),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryListResponse:
    _ = user
    memories = await _all_memories(db)
    roots = [item for item in memories if item.parent_id is None]
    if category:
        roots = [item for item in roots if item.category == category]
    roots.sort(
        key=lambda item: (
            bool(item.pinned),
            int(item.importance or 0),
            item.last_accessed_at or item.updated_at or item.created_at or _MIN_TIME,
        ),
        reverse=True,
    )
    return MemoryListResponse(items=[_memory_response(item) for item in roots], total=len(roots))


@router.get("/nodes/{id}")
async def get_memory_node(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryResponse:
    _ = user
    memory = await _get_memory(db, id)
    if memory is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    return _memory_response(memory)


@router.get("/nodes/{id}/children")
async def list_memory_children(
    id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryChildrenResponse:
    _ = user
    parent = await _get_memory(db, id)
    if parent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")

    memories = await _all_memories(db)
    children = [item for item in memories if item.parent_id == id]
    children.sort(key=lambda item: item.created_at or _MIN_TIME, reverse=True)
    trimmed = children[:limit]
    return MemoryChildrenResponse(parent_id=id, items=[_memory_response(item) for item in trimmed], total=len(children))


@router.patch("/nodes/{id}")
async def update_memory_node(
    id: UUID,
    payload: UpdateMemoryRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryResponse:
    _ = user
    memory = await _get_memory(db, id)
    if memory is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")

    updates = payload.model_dump(exclude_unset=True)
    if "parent_id" in updates:
        new_parent_id = updates["parent_id"]
        if new_parent_id == memory.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A node cannot be its own parent")
        if new_parent_id is not None:
            parent = await _get_memory(db, new_parent_id)
            if parent is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent memory not found")
            if _is_descendant(target_parent_id=new_parent_id, node_id=memory.id, memories=await _all_memories(db)):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot move node under its own descendant",
                )

    for key, value in updates.items():
        if key == "metadata":
            memory.metadata_json = value if isinstance(value, dict) else {}
            continue
        setattr(memory, key, value)

    if "content" in updates:
        embedding_service = getattr(request.app.state, "embedding_service", None)
        if embedding_service is not None:
            try:
                memory.embedding = await embedding_service.embed(memory.content)
            except Exception:  # noqa: BLE001
                pass

    await db.commit()
    await db.refresh(memory)
    return _memory_response(memory)


@router.post("/nodes/{id}/touch")
async def touch_memory_node(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryResponse:
    _ = user
    memory = await _get_memory(db, id)
    if memory is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    memory.last_accessed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(memory)
    return _memory_response(memory)


@router.get("/stats")
async def memory_stats(
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MemoryStatsResponse:
    _ = user
    memories = await _all_memories(db)
    categories: dict[str, int] = {}
    for item in memories:
        categories[item.category] = categories.get(item.category, 0) + 1
    return MemoryStatsResponse(total_memories=len(memories), categories=categories)


@router.delete("/{id}")
async def delete_memory(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    _ = user
    memory = await _get_memory(db, id)
    if memory is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")

    memories = await _all_memories(db)
    children_map = _children_map(memories)
    delete_ids = _descendant_ids(children_map, id)
    delete_ids.add(id)
    for node in memories:
        if node.id in delete_ids:
            await db.delete(node)
    await db.commit()
    return {"status": "deleted"}


def _memory_response(memory: Memory, *, score: float | None = None) -> MemoryResponse:
    return MemoryResponse(
        id=memory.id,
        content=memory.content,
        title=memory.title,
        summary=memory.summary,
        category=memory.category,
        parent_id=memory.parent_id,
        importance=memory.importance or 0,
        pinned=bool(memory.pinned),
        metadata=memory.metadata_json or {},
        session_id=memory.session_id,
        score=score,
        last_accessed_at=memory.last_accessed_at,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


def _score_for(memory_id: UUID, results: list) -> float | None:
    for item in results:
        if item.memory.id == memory_id:
            return item.score
    return None


async def _all_memories(db: AsyncSession) -> list[Memory]:
    result = await db.execute(select(Memory))
    return result.scalars().all()


async def _get_memory(db: AsyncSession, memory_id: UUID) -> Memory | None:
    result = await db.execute(select(Memory).where(Memory.id == memory_id))
    return result.scalars().first()


def _children_map(memories: list[Memory]) -> dict[UUID | None, list[Memory]]:
    mapping: dict[UUID | None, list[Memory]] = {}
    for memory in memories:
        mapping.setdefault(memory.parent_id, []).append(memory)
    return mapping


def _descendant_ids(mapping: dict[UUID | None, list[Memory]], root_id: UUID) -> set[UUID]:
    result: set[UUID] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        for child in mapping.get(current, []):
            if child.id in result:
                continue
            result.add(child.id)
            stack.append(child.id)
    return result


def _is_descendant(*, target_parent_id: UUID, node_id: UUID, memories: list[Memory]) -> bool:
    mapping = _children_map(memories)
    return node_id in _descendant_ids(mapping, target_parent_id)


def _filter_by_root(items: list[Memory], memories: list[Memory], root_id: UUID) -> list[Memory]:
    children = _children_map(memories)
    allowed = _descendant_ids(children, root_id)
    allowed.add(root_id)
    return [item for item in items if item.id in allowed]


def _apply_filters(
    memories: list[Memory],
    *,
    query: str | None = None,
    category: str | None = None,
    parent_id: UUID | None = None,
    root_id: UUID | None = None,
    roots_only: bool = False,
) -> list[Memory]:
    filtered = list(memories)
    if query:
        lowered = query.lower()
        filtered = [
            item
            for item in filtered
            if lowered in item.content.lower()
            or lowered in (item.title or "").lower()
            or lowered in (item.summary or "").lower()
        ]
    if category:
        filtered = [item for item in filtered if item.category == category]
    if parent_id is not None:
        filtered = [item for item in filtered if item.parent_id == parent_id]
    if roots_only:
        filtered = [item for item in filtered if item.parent_id is None]
    if root_id is not None:
        filtered = _filter_by_root(filtered, memories, root_id)
    return filtered
