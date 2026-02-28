from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Memory
from app.services.embeddings import EmbeddingService
from app.services.memory.repository import MemoryRepository
from app.services.memory.tree import (
    MIN_TIME,
    children_map,
    descendant_ids,
    expand_memory_branches,
    filter_by_root,
    is_descendant,
)
from app.services.memory.search import MemorySearchResult, MemorySearchService


class MemoryServiceError(Exception):
    """Base exception for memory domain/service failures."""


class MemoryNotFoundError(MemoryServiceError):
    """Memory node was not found."""


class ParentMemoryNotFoundError(MemoryServiceError):
    """Requested parent memory node was not found."""


class InvalidMemoryOperationError(MemoryServiceError):
    """Operation violates memory tree constraints."""


@dataclass(slots=True)
class MemoryQueryResult:
    items: list[Memory]
    total: int
    scores: dict[UUID, float] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryChildrenResult:
    parent_id: UUID
    items: list[Memory]
    total: int


class MemoryService:
    def __init__(self, repository: MemoryRepository) -> None:
        self._repo = repository

    async def list_memories(
        self,
        db: AsyncSession,
        *,
        query: str | None = None,
        category: str | None = None,
        parent_id: UUID | None = None,
        root_id: UUID | None = None,
        roots_only: bool = False,
        limit: int = 20,
        memory_search_service: MemorySearchService | None = None,
    ) -> MemoryQueryResult:
        safe_limit = max(1, min(limit, 200))
        if query and memory_search_service is not None:
            results = await memory_search_service.search(
                db,
                query,
                category=category,
                limit=safe_limit,
            )
            items = [item.memory for item in results]
            if root_id is not None:
                items = filter_by_root(items, await self._repo.list_all(db), root_id)
            if parent_id is not None:
                items = [item for item in items if item.parent_id == parent_id]
            if roots_only:
                items = [item for item in items if item.parent_id is None]
            return MemoryQueryResult(
                items=items,
                total=len(items),
                scores={item.memory.id: item.score for item in results},
            )

        memories = await self._repo.list_all(db)
        memories = self._apply_filters(
            memories,
            query=query,
            category=category,
            parent_id=parent_id,
            root_id=root_id,
            roots_only=roots_only,
        )
        memories.sort(key=lambda m: m.created_at or MIN_TIME, reverse=True)
        return MemoryQueryResult(items=memories[:safe_limit], total=len(memories))

    async def search_memories(
        self,
        db: AsyncSession,
        *,
        query: str,
        category: str | None = None,
        root_id: UUID | None = None,
        limit: int = 20,
        memory_search_service: MemorySearchService | None = None,
    ) -> MemoryQueryResult:
        safe_limit = max(1, min(limit, 200))
        if memory_search_service is not None:
            results = await memory_search_service.search(
                db,
                query,
                category=category,
                limit=safe_limit,
            )
            items = [item.memory for item in results]
            if root_id is not None:
                items = filter_by_root(items, await self._repo.list_all(db), root_id)
            return MemoryQueryResult(
                items=items,
                total=len(items),
                scores={item.memory.id: item.score for item in results},
            )

        memories = await self._repo.list_all(db)
        memories = self._apply_filters(
            memories,
            query=query,
            category=category,
            root_id=root_id,
        )
        memories.sort(key=lambda m: m.created_at or MIN_TIME, reverse=True)
        return MemoryQueryResult(items=memories[:safe_limit], total=len(memories))

    async def create_memory(
        self,
        db: AsyncSession,
        *,
        content: str,
        title: str | None,
        summary: str | None,
        category: str,
        parent_id: UUID | None,
        importance: int,
        pinned: bool,
        metadata: dict[str, Any],
        embedding: list[float] | None,
        embedding_service: EmbeddingService | None = None,
        ignore_embedding_errors: bool = False,
    ) -> Memory:
        if parent_id is not None:
            parent = await self._repo.get_by_id(db, parent_id)
            if parent is None:
                raise ParentMemoryNotFoundError("Parent memory not found")

        resolved_embedding = await self._resolve_embedding(
            content=content,
            embedding=embedding,
            embedding_service=embedding_service,
            ignore_errors=ignore_embedding_errors,
        )

        memory = Memory(
            content=content,
            title=title,
            summary=summary,
            category=category,
            parent_id=parent_id,
            importance=importance,
            pinned=pinned,
            metadata_json=metadata,
            embedding=resolved_embedding,
        )
        return await self._repo.create(db, memory)

    async def list_root_memories(
        self,
        db: AsyncSession,
        *,
        category: str | None = None,
    ) -> list[Memory]:
        memories = await self._repo.list_all(db)
        roots = [item for item in memories if item.parent_id is None]
        if category:
            roots = [item for item in roots if item.category == category]
        roots.sort(
            key=lambda item: (
                bool(item.pinned),
                int(item.importance or 0),
                item.last_accessed_at or item.updated_at or item.created_at or MIN_TIME,
            ),
            reverse=True,
        )
        return roots

    async def get_memory(self, db: AsyncSession, memory_id: UUID) -> Memory:
        memory = await self._repo.get_by_id(db, memory_id)
        if memory is None:
            raise MemoryNotFoundError("Memory not found")
        return memory

    async def list_children(
        self,
        db: AsyncSession,
        *,
        parent_id: UUID,
        limit: int = 100,
    ) -> MemoryChildrenResult:
        await self.get_memory(db, parent_id)
        memories = await self._repo.list_all(db)
        children = [item for item in memories if item.parent_id == parent_id]
        children.sort(key=lambda item: item.created_at or MIN_TIME, reverse=True)
        trimmed = children[: max(1, min(limit, 500))]
        return MemoryChildrenResult(parent_id=parent_id, items=trimmed, total=len(children))

    async def update_memory(
        self,
        db: AsyncSession,
        *,
        memory_id: UUID,
        updates: dict[str, Any],
        embedding_service: EmbeddingService | None = None,
        ignore_embedding_errors: bool = False,
    ) -> Memory:
        memory = await self.get_memory(db, memory_id)

        if "parent_id" in updates:
            new_parent_id = updates["parent_id"]
            if new_parent_id == memory.id:
                raise InvalidMemoryOperationError("A node cannot be its own parent")
            if new_parent_id is not None:
                parent = await self._repo.get_by_id(db, new_parent_id)
                if parent is None:
                    raise ParentMemoryNotFoundError("Parent memory not found")
                if is_descendant(
                    target_parent_id=new_parent_id,
                    node_id=memory.id,
                    memories=await self._repo.list_all(db),
                ):
                    raise InvalidMemoryOperationError("Cannot move node under its own descendant")

        for key, value in updates.items():
            if key == "metadata":
                memory.metadata_json = value if isinstance(value, dict) else {}
                continue
            setattr(memory, key, value)

        if "content" in updates:
            memory.embedding = await self._resolve_embedding(
                content=memory.content,
                embedding=None,
                embedding_service=embedding_service,
                ignore_errors=ignore_embedding_errors,
            )

        return await self._repo.save(db, memory)

    async def touch_memory(self, db: AsyncSession, memory_id: UUID) -> Memory:
        memory = await self.get_memory(db, memory_id)
        memory.last_accessed_at = datetime.now(UTC)
        return await self._repo.save(db, memory)

    async def memory_stats(self, db: AsyncSession) -> dict[str, int]:
        memories = await self._repo.list_all(db)
        categories: dict[str, int] = {}
        for item in memories:
            categories[item.category] = categories.get(item.category, 0) + 1
        return categories

    async def delete_memory(self, db: AsyncSession, memory_id: UUID) -> None:
        await self.get_memory(db, memory_id)
        memories = await self._repo.list_all(db)
        child_index = children_map(memories)
        delete_ids = descendant_ids(child_index, memory_id)
        delete_ids.add(memory_id)
        await self._repo.delete_by_ids(db, memories, delete_ids)

    async def expand_branches(
        self,
        db: AsyncSession,
        *,
        items: list[Memory],
        root_id: UUID | None = None,
    ) -> list[Memory]:
        memories = await self._repo.list_all(db)
        expanded = expand_memory_branches(items, memories)
        if root_id is not None:
            expanded = filter_by_root(expanded, memories, root_id)
        return expanded

    def _apply_filters(
        self,
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
            filtered = filter_by_root(filtered, memories, root_id)
        return filtered

    async def _resolve_embedding(
        self,
        *,
        content: str,
        embedding: list[float] | None,
        embedding_service: EmbeddingService | None,
        ignore_errors: bool,
    ) -> list[float] | None:
        if embedding is not None or embedding_service is None:
            return embedding
        try:
            return await embedding_service.embed(content)
        except Exception:  # noqa: BLE001
            if ignore_errors:
                return None
            raise
