from __future__ import annotations

from contextlib import asynccontextmanager
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Memory
from app.schemas.memory import (
    MemoryBackupDocument,
    MemoryBackupImportRequest,
    MemoryBackupImportResponse,
    MemoryBackupNode,
)
from app.services.memory import MemoryService
from app.services.memory.system import SYSTEM_MEMORY_SPECS

BACKUP_SCHEMA_VERSION = "memory_backup_v1"


class MemoryBackupServiceError(Exception):
    """Base backup service error."""


class InvalidMemoryBackupError(MemoryBackupServiceError):
    """Backup document is invalid."""


@dataclass(slots=True)
class _NodeCtx:
    node: MemoryBackupNode
    system_key: str | None


class MemoryBackupService:
    def __init__(self, memory_service: MemoryService) -> None:
        self._memory_service = memory_service
        self._system_title_to_key = {item.title: item.key for item in SYSTEM_MEMORY_SPECS}

    async def export_document(
        self,
        db: AsyncSession,
        *,
        include_system: bool = True,
    ) -> MemoryBackupDocument:
        memories = await self._memory_service.list_all_memories(db)
        memories.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC))

        nodes: list[MemoryBackupNode] = []
        included_ids: set[UUID] = set()
        for item in memories:
            if not include_system and bool(item.is_system):
                continue
            included_ids.add(item.id)

        for item in memories:
            if item.id not in included_ids:
                continue
            parent_external_id = (
                str(item.parent_id)
                if item.parent_id is not None and item.parent_id in included_ids
                else None
            )
            nodes.append(
                MemoryBackupNode(
                    external_id=str(item.id),
                    parent_external_id=parent_external_id,
                    content=item.content,
                    title=item.title,
                    summary=item.summary,
                    category=item.category,
                    importance=int(item.importance or 0),
                    pinned=bool(item.pinned),
                    is_system=bool(item.is_system),
                    system_key=item.system_key,
                    metadata=item.metadata_json or {},
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
            )

        return MemoryBackupDocument(
            schema_version=BACKUP_SCHEMA_VERSION,
            exported_at=datetime.now(UTC),
            nodes=nodes,
        )

    async def import_document(
        self,
        db: AsyncSession,
        *,
        request: MemoryBackupImportRequest,
    ) -> MemoryBackupImportResponse:
        document = request.document
        if document.schema_version != BACKUP_SCHEMA_VERSION:
            raise InvalidMemoryBackupError(
                f"Unsupported schema_version '{document.schema_version}'"
            )

        ordered_nodes = self._validate_and_order_nodes(document.nodes)
        async with self._transaction_scope(db):
            response = await self._import_document_in_transaction(
                db,
                ordered_nodes=ordered_nodes,
                request=request,
            )
        return response

    async def _import_document_in_transaction(
        self,
        db: AsyncSession,
        *,
        ordered_nodes: list[_NodeCtx],
        request: MemoryBackupImportRequest,
    ) -> MemoryBackupImportResponse:
        external_to_internal: dict[str, UUID] = {}
        deleted = await self._apply_replace_mode(db, mode=request.mode, commit=False)
        effective_system_policy = (
            "replace_explicit" if request.mode == "replace_all" else "keep_existing"
        )
        created = 0
        updated = 0
        skipped = 0

        memories = await self._memory_service.list_all_memories(db)
        by_backup_external = self._index_by_backup_external_id(memories)

        for ctx in ordered_nodes:
            node = ctx.node
            if node.is_system:
                outcome = await self._import_system_node(
                    db,
                    node=node,
                    system_key=ctx.system_key,
                    system_policy=effective_system_policy,
                    commit=False,
                )
                external_to_internal[node.external_id] = outcome.memory.id
                if outcome.action == "created":
                    created += 1
                elif outcome.action == "updated":
                    updated += 1
                else:
                    skipped += 1
                continue

            parent_id: UUID | None = None
            if node.parent_external_id is not None:
                parent_id = external_to_internal.get(node.parent_external_id)
                if parent_id is None:
                    raise InvalidMemoryBackupError(
                        f"Parent external_id '{node.parent_external_id}' could not be resolved during import"
                    )

            metadata = dict(node.metadata or {})
            metadata["backup_external_id"] = node.external_id

            existing = by_backup_external.get(node.external_id)
            if existing is not None:
                updates_payload = {
                    "content": node.content,
                    "title": node.title,
                    "summary": node.summary,
                    "category": node.category,
                    "importance": int(node.importance),
                    "pinned": bool(node.pinned),
                    "parent_id": parent_id,
                    "metadata": metadata,
                }
                updated_node = await self._memory_service.update_memory(
                    db,
                    memory_id=existing.id,
                    updates=updates_payload,
                    embedding_service=None,
                    ignore_embedding_errors=True,
                    commit=False,
                )
                external_to_internal[node.external_id] = updated_node.id
                by_backup_external[node.external_id] = updated_node
                updated += 1
                continue

            created_node = await self._memory_service.create_memory(
                db,
                content=node.content,
                title=node.title,
                summary=node.summary,
                category=node.category,
                parent_id=parent_id,
                importance=int(node.importance),
                pinned=bool(node.pinned),
                metadata=metadata,
                embedding=None,
                embedding_service=None,
                ignore_embedding_errors=True,
                commit=False,
            )
            external_to_internal[node.external_id] = created_node.id
            by_backup_external[node.external_id] = created_node
            created += 1

        return MemoryBackupImportResponse(
            total_in_backup=len(request.document.nodes),
            created=created,
            updated=updated,
            deleted=deleted,
            skipped=skipped,
        )

    @asynccontextmanager
    async def _transaction_scope(self, db: AsyncSession):
        in_transaction = getattr(db, "in_transaction", None)
        begin_nested = getattr(db, "begin_nested", None)
        begin = getattr(db, "begin", None)
        if callable(in_transaction) and in_transaction() and callable(begin_nested):
            async with begin_nested():
                yield
            return
        if callable(begin):
            async with begin():
                yield
            return
        yield

    async def _apply_replace_mode(self, db: AsyncSession, *, mode: str, commit: bool) -> int:
        if mode == "merge":
            return 0

        if mode == "replace_non_system":
            return await self._memory_service.purge_memories(
                db,
                include_system=False,
                commit=commit,
            )
        elif mode == "replace_all":
            return await self._memory_service.purge_memories(
                db,
                include_system=True,
                commit=commit,
            )
        else:
            raise InvalidMemoryBackupError(f"Unsupported import mode '{mode}'")

    @dataclass(slots=True)
    class _ImportOutcome:
        action: str
        memory: Memory

    async def _import_system_node(
        self,
        db: AsyncSession,
        *,
        node: MemoryBackupNode,
        system_key: str | None,
        system_policy: str,
        commit: bool,
    ) -> _ImportOutcome:
        key = (system_key or "").strip()
        if not key:
            raise InvalidMemoryBackupError(
                f"System node '{node.external_id}' is missing system_key"
            )

        if system_policy == "replace_explicit":
            allow_legacy_title_fallback = False
        elif system_policy in {"keep_existing", "upsert_by_system_key"}:
            allow_legacy_title_fallback = True
        else:
            raise InvalidMemoryBackupError(f"Unsupported system_policy '{system_policy}'")

        existing = await self._find_existing_system_memory(
            db,
            key=key,
            title=node.title,
            allow_legacy_title_fallback=allow_legacy_title_fallback,
        )
        if existing is not None and system_policy == "keep_existing":
            return self._ImportOutcome(action="skipped", memory=existing)

        before_existing = existing
        memory = await self._memory_service.upsert_system_memory(
            db,
            system_key=key,
            title=node.title or key,
            content=node.content,
            importance=int(node.importance),
            metadata=node.metadata,
            allow_legacy_title_fallback=allow_legacy_title_fallback,
            commit=commit,
        )
        if before_existing is None:
            action = "created"
        else:
            action = "skipped"

        updates: dict[str, object] = {}
        if memory.content != node.content:
            updates["content"] = node.content
        if memory.summary != node.summary:
            updates["summary"] = node.summary
        if (memory.title or "") != (node.title or "") and node.title is not None:
            updates["title"] = node.title
        if int(memory.importance or 0) != int(node.importance):
            updates["importance"] = int(node.importance)
        if not bool(memory.pinned):
            updates["pinned"] = True
        if memory.parent_id is not None:
            updates["parent_id"] = None
        if memory.category != "core":
            updates["category"] = "core"
        if updates:
            memory = await self._memory_service.update_memory(
                db,
                memory_id=memory.id,
                updates=updates,
                embedding_service=None,
                ignore_embedding_errors=True,
                commit=commit,
            )
            action = "updated" if action != "created" else "created"

        return self._ImportOutcome(action=action, memory=memory)

    async def _find_existing_system_memory(
        self,
        db: AsyncSession,
        *,
        key: str,
        title: str | None,
        allow_legacy_title_fallback: bool,
    ) -> Memory | None:
        memories = await self._memory_service.list_all_memories(db)
        normalized_key = key.strip()
        for item in memories:
            if bool(item.is_system) and str(item.system_key or "").strip() == normalized_key:
                return item
        if not allow_legacy_title_fallback:
            return None
        legacy_title = (title or "").strip()
        if not legacy_title:
            return None
        for item in memories:
            if (
                item.parent_id is None
                and item.category == "core"
                and (item.title or "").strip() == legacy_title
            ):
                return item
        return None

    def _index_by_backup_external_id(self, memories: list[Memory]) -> dict[str, Memory]:
        indexed: dict[str, Memory] = {}
        for item in memories:
            metadata = item.metadata_json if isinstance(item.metadata_json, dict) else {}
            external_id = str(metadata.get("backup_external_id") or "").strip()
            if external_id:
                indexed[external_id] = item
        return indexed

    def _validate_and_order_nodes(self, nodes: list[MemoryBackupNode]) -> list[_NodeCtx]:
        by_external: dict[str, _NodeCtx] = {}
        children_by_parent: dict[str | None, list[str]] = {}
        indegree: dict[str, int] = {}

        for node in nodes:
            external_id = node.external_id.strip()
            if not external_id:
                raise InvalidMemoryBackupError("Node has empty external_id")
            if external_id in by_external:
                raise InvalidMemoryBackupError(f"Duplicate external_id '{external_id}'")

            system_key = self._normalized_system_key(node)
            if node.is_system and system_key is None:
                raise InvalidMemoryBackupError(
                    f"System node '{external_id}' must include a valid system_key"
                )
            if (not node.is_system) and node.system_key is not None:
                raise InvalidMemoryBackupError(
                    f"Non-system node '{external_id}' cannot define system_key"
                )

            ctx = _NodeCtx(node=node, system_key=system_key)
            by_external[external_id] = ctx
            indegree[external_id] = 0

        for external_id, ctx in by_external.items():
            parent_external = (
                ctx.node.parent_external_id.strip()
                if isinstance(ctx.node.parent_external_id, str)
                else None
            )
            if parent_external is not None:
                if parent_external == external_id:
                    raise InvalidMemoryBackupError(
                        f"Node '{external_id}' cannot reference itself as parent"
                    )
                if parent_external not in by_external:
                    raise InvalidMemoryBackupError(
                        f"Node '{external_id}' references unknown parent_external_id '{parent_external}'"
                    )
                indegree[external_id] += 1
                children_by_parent.setdefault(parent_external, []).append(external_id)
            else:
                children_by_parent.setdefault(None, []).append(external_id)

        ordered_ids: list[str] = []
        ready = deque([node_id for node_id, degree in indegree.items() if degree == 0])
        while ready:
            node_id = ready.popleft()
            ordered_ids.append(node_id)
            for child_id in children_by_parent.get(node_id, []):
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    ready.append(child_id)

        if len(ordered_ids) != len(by_external):
            raise InvalidMemoryBackupError("Backup document contains circular parent references")

        return [by_external[node_id] for node_id in ordered_ids]

    def _normalized_system_key(self, node: MemoryBackupNode) -> str | None:
        key = (node.system_key or "").strip()
        if key:
            return key
        if not node.is_system:
            return None
        title = (node.title or "").strip()
        return self._system_title_to_key.get(title)
