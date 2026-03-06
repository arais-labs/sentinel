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


class ProtectedMemoryOperationError(InvalidMemoryOperationError):
    """Operation is blocked for backend-protected system memories."""


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

    async def list_all_memories(self, db: AsyncSession) -> list[Memory]:
        return await self._repo.list_all(db)

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
        commit: bool = True,
    ) -> Memory:
        if parent_id is not None:
            parent = await self._repo.get_by_id(db, parent_id)
            if parent is None:
                raise ParentMemoryNotFoundError("Parent memory not found")
            if self._is_system_memory(parent):
                raise ProtectedMemoryOperationError(
                    "Cannot attach child nodes under a protected system memory"
                )

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
        return await self._repo.create(db, memory, commit=commit)

    async def upsert_system_memory(
        self,
        db: AsyncSession,
        *,
        system_key: str,
        title: str,
        content: str,
        importance: int,
        metadata: dict[str, Any] | None = None,
        allow_legacy_title_fallback: bool = True,
        commit: bool = True,
    ) -> Memory:
        key = (system_key or "").strip()
        if not key:
            raise InvalidMemoryOperationError("system_key must be a non-empty string")

        memories = await self._repo.list_all(db)
        current = next(
            (
                item
                for item in memories
                if bool(item.is_system) and str(item.system_key or "").strip() == key
            ),
            None,
        )
        if current is None and allow_legacy_title_fallback:
            current = next(
                (
                    item
                    for item in memories
                    if item.parent_id is None and item.category == "core" and (item.title or "").strip() == title
                ),
                None,
            )

        if current is None:
            created = Memory(
                content=content,
                title=title,
                summary=None,
                category="core",
                parent_id=None,
                importance=int(importance),
                pinned=True,
                is_system=True,
                system_key=key,
                metadata_json=metadata or {},
                embedding=None,
            )
            return await self._repo.create(db, created, commit=commit)

        updates: dict[str, Any] = {}
        if not bool(current.is_system):
            updates["is_system"] = True
        if str(current.system_key or "").strip() != key:
            updates["system_key"] = key
        if not bool(current.pinned):
            updates["pinned"] = True
        if current.parent_id is not None:
            updates["parent_id"] = None
        if current.category != "core":
            updates["category"] = "core"
        if (current.title or "").strip() != title:
            updates["title"] = title
        if int(current.importance or 0) != int(importance):
            updates["importance"] = int(importance)

        if not updates:
            return current
        return await self.update_memory(
            db,
            memory_id=current.id,
            updates=updates,
            embedding_service=None,
            ignore_embedding_errors=True,
            commit=commit,
        )

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

    async def move_memories(
        self,
        db: AsyncSession,
        *,
        node_ids: list[UUID],
        target_parent_id: UUID | None,
        to_root: bool = False,
        commit: bool = True,
    ) -> list[Memory]:
        if not node_ids:
            raise InvalidMemoryOperationError("node_ids must contain at least one node")
        if to_root and target_parent_id is not None:
            raise InvalidMemoryOperationError("Provide either to_root=true or target_parent_id, not both")
        if not to_root and target_parent_id is None:
            raise InvalidMemoryOperationError("Provide target_parent_id or set to_root=true")

        deduped_ids: list[UUID] = []
        seen: set[UUID] = set()
        for node_id in node_ids:
            if node_id in seen:
                continue
            seen.add(node_id)
            deduped_ids.append(node_id)

        memories = await self._repo.list_all(db)
        by_id = {item.id: item for item in memories}

        missing_ids = [str(node_id) for node_id in deduped_ids if node_id not in by_id]
        if missing_ids:
            raise MemoryNotFoundError("Memory node(s) not found: " + ", ".join(missing_ids))

        if target_parent_id is not None:
            parent = by_id.get(target_parent_id)
            if parent is None:
                raise ParentMemoryNotFoundError("Parent memory not found")
            if self._is_system_memory(parent):
                raise ProtectedMemoryOperationError(
                    "Cannot move nodes under a protected system memory"
                )

        if any(self._is_system_memory(by_id[node_id]) for node_id in deduped_ids):
            raise ProtectedMemoryOperationError("Cannot move protected system memory nodes")

        node_id_set = set(deduped_ids)
        for ancestor in deduped_ids:
            for maybe_child in deduped_ids:
                if ancestor == maybe_child:
                    continue
                if is_descendant(
                    target_parent_id=ancestor,
                    node_id=maybe_child,
                    memories=memories,
                ):
                    raise InvalidMemoryOperationError(
                        "node_ids contains both an ancestor and its descendant; move only top-level nodes"
                    )

        if target_parent_id is not None:
            for node_id in deduped_ids:
                if node_id == target_parent_id:
                    raise InvalidMemoryOperationError("A node cannot be moved under itself")
                if target_parent_id in node_id_set:
                    raise InvalidMemoryOperationError(
                        "target_parent_id cannot be one of the moved node_ids"
                    )
                if is_descendant(
                    target_parent_id=node_id,
                    node_id=target_parent_id,
                    memories=memories,
                ):
                    raise InvalidMemoryOperationError(
                        "Cannot move a node under its own descendant"
                    )

        destination_parent_id = None if to_root else target_parent_id
        moved: list[Memory] = []
        for node_id in deduped_ids:
            node = by_id[node_id]
            if node.parent_id == destination_parent_id:
                moved.append(node)
                continue
            node.parent_id = destination_parent_id
            node.updated_at = datetime.now(UTC)
            db.add(node)
            moved.append(node)

        if commit:
            await db.commit()
        else:
            await db.flush()
        return moved

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
        commit: bool = True,
    ) -> Memory:
        memory = await self.get_memory(db, memory_id)
        self._validate_system_memory_updates(memory, updates)

        if "parent_id" in updates:
            new_parent_id = updates["parent_id"]
            if new_parent_id == memory.id:
                raise InvalidMemoryOperationError("A node cannot be its own parent")
            if new_parent_id is not None:
                parent = await self._repo.get_by_id(db, new_parent_id)
                if parent is None:
                    raise ParentMemoryNotFoundError("Parent memory not found")
                if self._is_system_memory(parent):
                    raise ProtectedMemoryOperationError(
                        "Cannot move nodes under a protected system memory"
                    )
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

        return await self._repo.save(db, memory, commit=commit)

    async def touch_memory(self, db: AsyncSession, memory_id: UUID, *, commit: bool = True) -> Memory:
        memory = await self.get_memory(db, memory_id)
        memory.last_accessed_at = datetime.now(UTC)
        return await self._repo.save(db, memory, commit=commit)

    async def memory_stats(self, db: AsyncSession) -> dict[str, int]:
        memories = await self._repo.list_all(db)
        categories: dict[str, int] = {}
        for item in memories:
            categories[item.category] = categories.get(item.category, 0) + 1
        return categories

    async def delete_memory(self, db: AsyncSession, memory_id: UUID, *, commit: bool = True) -> None:
        memory = await self.get_memory(db, memory_id)
        if self._is_system_memory(memory):
            raise ProtectedMemoryOperationError("Cannot delete a protected system memory")
        memories = await self._repo.list_all(db)
        child_index = children_map(memories)
        delete_ids = descendant_ids(child_index, memory_id)
        delete_ids.add(memory_id)
        await self._repo.delete_by_ids(db, memories, delete_ids, commit=commit)

    async def purge_memories(
        self,
        db: AsyncSession,
        *,
        include_system: bool,
        commit: bool = True,
    ) -> int:
        memories = await self._repo.list_all(db)
        delete_ids = {
            item.id
            for item in memories
            if include_system or not self._is_system_memory(item)
        }
        if not delete_ids:
            return 0
        await self._repo.delete_by_ids(db, memories, delete_ids, commit=commit)
        return len(delete_ids)

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

    @staticmethod
    def _is_system_memory(memory: Memory) -> bool:
        return bool(getattr(memory, "is_system", False))

    def _validate_system_memory_updates(
        self,
        memory: Memory,
        updates: dict[str, Any],
    ) -> None:
        if not self._is_system_memory(memory):
            return
        if "pinned" in updates and updates.get("pinned") is False:
            raise ProtectedMemoryOperationError("Cannot unpin a protected system memory")
        if "parent_id" in updates and updates.get("parent_id") is not None:
            raise ProtectedMemoryOperationError("System memories must stay at root level")
        if "category" in updates and updates.get("category") != memory.category:
            raise ProtectedMemoryOperationError("Cannot change category of a protected system memory")
