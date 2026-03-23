"""Native module: memory — hierarchical memory tree operations."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.database.database import AsyncSessionLocal
from app.models import Memory
from app.services.araios.runtime_services import (
    get_embedding_service,
    get_memory_search_service,
)
from app.services.memory import (
    InvalidMemoryOperationError,
    MemoryNotFoundError,
    MemoryRepository,
    MemoryService,
    ParentMemoryNotFoundError,
)
from app.services.tools.executor import ToolValidationError

_ALLOWED_MEMORY_CATEGORIES = {"core", "preference", "project", "correction"}
ALLOWED_MEMORY_COMMANDS = (
    "store",
    "roots",
    "tree",
    "get_node",
    "list_children",
    "update",
    "touch",
    "move",
    "delete",
    "search",
)

# ── Helpers ──


def _memory_as_dict(memory: Memory, *, include_parent: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(memory.id),
        "content": memory.content,
        "title": memory.title,
        "summary": memory.summary,
        "category": memory.category,
        "importance": int(memory.importance or 0),
        "pinned": bool(memory.pinned),
        "is_system": bool(getattr(memory, "is_system", False)),
        "system_key": getattr(memory, "system_key", None),
    }
    if include_parent:
        data["parent_id"] = str(memory.parent_id) if memory.parent_id else None
    return data


def _raise_memory_tool_validation_error(
    exc: Exception,
    *,
    not_found_detail: str,
    parent_not_found_detail: str = "Parent memory node not found",
) -> None:
    if isinstance(exc, MemoryNotFoundError):
        raise ToolValidationError(not_found_detail) from exc
    if isinstance(exc, ParentMemoryNotFoundError):
        raise ToolValidationError(parent_not_found_detail) from exc
    if isinstance(exc, InvalidMemoryOperationError):
        raise ToolValidationError(str(exc)) from exc
    raise exc


# ---------------------------------------------------------------------------
# Handler functions (module-level)
# ---------------------------------------------------------------------------


async def handle_store(payload: dict[str, Any]) -> dict[str, Any]:
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ToolValidationError("Field 'content' must be a non-empty string")

    category = payload.get("category", "project")
    if not isinstance(category, str) or category not in _ALLOWED_MEMORY_CATEGORIES:
        raise ToolValidationError(
            "Field 'category' must be one of: core, preference, project, correction"
        )

    title = payload.get("title")
    if title is not None and not isinstance(title, str):
        raise ToolValidationError("Field 'title' must be a string")
    title = title.strip() if isinstance(title, str) else None
    if title == "":
        title = None

    summary = payload.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise ToolValidationError("Field 'summary' must be a string")
    summary = summary.strip() if isinstance(summary, str) else None
    if summary == "":
        summary = None

    parent_id_raw = payload.get("parent_id")
    parent_id: UUID | None = None
    if parent_id_raw is not None:
        if not isinstance(parent_id_raw, str) or not parent_id_raw.strip():
            raise ToolValidationError("Field 'parent_id' must be a UUID string")
        try:
            parent_id = UUID(parent_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'parent_id' must be a valid UUID string") from exc

    importance = payload.get("importance", 0)
    if (
        not isinstance(importance, int)
        or isinstance(importance, bool)
        or importance < 0
        or importance > 100
    ):
        raise ToolValidationError("Field 'importance' must be an integer between 0 and 100")

    pinned = payload.get("pinned", False)
    if not isinstance(pinned, bool):
        raise ToolValidationError("Field 'pinned' must be a boolean")

    metadata = payload.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ToolValidationError("Field 'metadata' must be an object")

    embedding = payload.get("embedding")
    if embedding is not None:
        if not isinstance(embedding, list) or not all(
            isinstance(x, (int, float)) for x in embedding
        ):
            raise ToolValidationError("Field 'embedding' must be a list of numbers")
        embedding = [float(x) for x in embedding]

    memory_service = MemoryService(MemoryRepository())
    try:
        async with AsyncSessionLocal() as db:
            memory = await memory_service.create_memory(
                db,
                content=content.strip(),
                title=title,
                summary=summary,
                category=category,
                parent_id=parent_id,
                importance=importance,
                pinned=pinned,
                metadata=metadata,
                embedding=embedding,
                embedding_service=get_embedding_service(),
                ignore_embedding_errors=False,
            )
    except Exception as exc:  # noqa: BLE001
        _raise_memory_tool_validation_error(
            exc,
            not_found_detail="Memory node not found",
            parent_not_found_detail="Parent memory node not found",
        )
        raise

    return {
        **_memory_as_dict(memory),
        "embedded": memory.embedding is not None,
    }


async def handle_roots(payload: dict[str, Any]) -> dict[str, Any]:
    if payload:
        raise ToolValidationError("memory_roots does not accept input fields")
    memory_service = MemoryService(MemoryRepository())
    async with AsyncSessionLocal() as db:
        roots = await memory_service.list_root_memories(db)
    return {
        "items": [
            {
                **_memory_as_dict(item, include_parent=False),
            }
            for item in roots
        ],
        "total": len(roots),
    }


async def handle_tree(payload: dict[str, Any]) -> dict[str, Any]:
    category = payload.get("category")
    if category is not None:
        if not isinstance(category, str) or category not in _ALLOWED_MEMORY_CATEGORIES:
            raise ToolValidationError(
                "Field 'category' must be one of: core, preference, project, correction"
            )

    root_id_raw = payload.get("root_id")
    root_id: UUID | None = None
    if root_id_raw is not None:
        if not isinstance(root_id_raw, str) or not root_id_raw.strip():
            raise ToolValidationError("Field 'root_id' must be a UUID string")
        try:
            root_id = UUID(root_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'root_id' must be a valid UUID string") from exc

    max_depth = payload.get("max_depth", 5)
    if (
        not isinstance(max_depth, int)
        or isinstance(max_depth, bool)
        or max_depth < 0
        or max_depth > 20
    ):
        raise ToolValidationError("Field 'max_depth' must be an integer between 0 and 20")

    include_content = payload.get("include_content", False)
    if not isinstance(include_content, bool):
        raise ToolValidationError("Field 'include_content' must be a boolean")

    memory_service = MemoryService(MemoryRepository())
    async with AsyncSessionLocal() as db:
        all_items = await memory_service.list_all_memories(db)
        if category is not None and root_id is None:
            all_items = [item for item in all_items if item.category == category]
        by_id = {item.id: item for item in all_items}
        children_by_parent: dict[UUID | None, list[Memory]] = {}
        for item in all_items:
            children_by_parent.setdefault(item.parent_id, []).append(item)
        for children in children_by_parent.values():
            children.sort(
                key=lambda item: (
                    item.created_at or datetime.min.replace(tzinfo=UTC),
                    item.id,
                ),
                reverse=True,
            )

        if root_id is not None:
            root = by_id.get(root_id)
            if root is None:
                raise ToolValidationError("root_id references unknown memory node")
            roots = [root]
        else:
            roots = await memory_service.list_root_memories(db, category=category)

    visible_nodes = 0
    truncated = False

    def _node_to_tree(node: Memory, depth: int) -> dict[str, Any]:
        nonlocal visible_nodes, truncated
        visible_nodes += 1
        direct_children = children_by_parent.get(node.id, [])
        has_more_children = depth >= max_depth and bool(direct_children)
        if has_more_children:
            truncated = True

        payload_node: dict[str, Any] = {
            "id": str(node.id),
            "parent_id": str(node.parent_id) if node.parent_id else None,
            "title": node.title,
            "summary": node.summary,
            "category": node.category,
            "importance": int(node.importance or 0),
            "pinned": bool(node.pinned),
            "depth": depth,
            "child_count": len(direct_children),
            "has_more_children": has_more_children,
            "children": [],
        }
        if include_content:
            payload_node["content"] = node.content

        if depth < max_depth and direct_children:
            payload_node["children"] = [
                _node_to_tree(child, depth + 1)
                for child in direct_children
            ]

        return payload_node

    tree_roots = [_node_to_tree(root, 0) for root in roots]
    return {
        "roots": tree_roots,
        "total_roots": len(tree_roots),
        "visible_nodes": visible_nodes,
        "max_depth": max_depth,
        "truncated": truncated,
    }


async def handle_get_node(payload: dict[str, Any]) -> dict[str, Any]:
    node_id_raw = payload.get("id")
    if not isinstance(node_id_raw, str) or not node_id_raw.strip():
        raise ToolValidationError("Field 'id' must be a non-empty UUID string")
    try:
        node_id = UUID(node_id_raw.strip())
    except ValueError as exc:
        raise ToolValidationError("Field 'id' must be a valid UUID string") from exc
    memory_service = MemoryService(MemoryRepository())
    try:
        async with AsyncSessionLocal() as db:
            node = await memory_service.touch_memory(db, node_id)
    except Exception as exc:  # noqa: BLE001
        _raise_memory_tool_validation_error(exc, not_found_detail="Memory node not found")
        raise
    return {
        **_memory_as_dict(node),
        "metadata": node.metadata_json or {},
    }


async def handle_list_children(payload: dict[str, Any]) -> dict[str, Any]:
    parent_id_raw = payload.get("parent_id")
    if not isinstance(parent_id_raw, str) or not parent_id_raw.strip():
        raise ToolValidationError("Field 'parent_id' must be a non-empty UUID string")
    try:
        parent_id = UUID(parent_id_raw.strip())
    except ValueError as exc:
        raise ToolValidationError("Field 'parent_id' must be a valid UUID string") from exc
    memory_service = MemoryService(MemoryRepository())
    try:
        async with AsyncSessionLocal() as db:
            result = await memory_service.list_children(db, parent_id=parent_id)
    except Exception as exc:  # noqa: BLE001
        _raise_memory_tool_validation_error(
            exc,
            not_found_detail="Memory node not found",
            parent_not_found_detail="Parent memory node not found",
        )
        raise
    return {
        "parent_id": str(parent_id),
        "items": [
            {
                **_memory_as_dict(item, include_parent=False),
            }
            for item in result.items
        ],
        "total": result.total,
    }


async def handle_update(payload: dict[str, Any]) -> dict[str, Any]:
    node_id_raw = payload.get("id")
    if not isinstance(node_id_raw, str) or not node_id_raw.strip():
        raise ToolValidationError("Field 'id' must be a non-empty UUID string")
    try:
        node_id = UUID(node_id_raw.strip())
    except ValueError as exc:
        raise ToolValidationError("Field 'id' must be a valid UUID string") from exc

    allowed_updates = {
        "content",
        "title",
        "summary",
        "category",
        "parent_id",
        "importance",
        "pinned",
        "metadata",
    }
    unknown = [key for key in payload if key not in allowed_updates and key != "id"]
    if unknown:
        raise ToolValidationError(f"Unknown update fields: {', '.join(sorted(unknown))}")

    updates: dict[str, Any] = {}

    if "content" in payload:
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ToolValidationError("Field 'content' must be a non-empty string")
        updates["content"] = content.strip()

    if "title" in payload:
        title = payload.get("title")
        if title is not None and not isinstance(title, str):
            raise ToolValidationError("Field 'title' must be a string or null")
        updates["title"] = title.strip() if isinstance(title, str) and title.strip() else None

    if "summary" in payload:
        summary = payload.get("summary")
        if summary is not None and not isinstance(summary, str):
            raise ToolValidationError("Field 'summary' must be a string or null")
        updates["summary"] = (
            summary.strip() if isinstance(summary, str) and summary.strip() else None
        )

    if "category" in payload:
        category = payload.get("category")
        if not isinstance(category, str) or category not in _ALLOWED_MEMORY_CATEGORIES:
            raise ToolValidationError(
                "Field 'category' must be one of: core, preference, project, correction"
            )
        updates["category"] = category

    if "importance" in payload:
        importance = payload.get("importance")
        if (
            not isinstance(importance, int)
            or isinstance(importance, bool)
            or importance < 0
            or importance > 100
        ):
            raise ToolValidationError("Field 'importance' must be an integer between 0 and 100")
        updates["importance"] = importance

    if "pinned" in payload:
        pinned = payload.get("pinned")
        if not isinstance(pinned, bool):
            raise ToolValidationError("Field 'pinned' must be a boolean")
        updates["pinned"] = pinned

    if "metadata" in payload:
        metadata = payload.get("metadata")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ToolValidationError("Field 'metadata' must be an object")
        updates["metadata"] = metadata

    if "parent_id" in payload:
        parent_id_raw = payload.get("parent_id")
        if parent_id_raw is None:
            updates["parent_id"] = None
        else:
            if not isinstance(parent_id_raw, str) or not parent_id_raw.strip():
                raise ToolValidationError("Field 'parent_id' must be a UUID string or null")
            try:
                updates["parent_id"] = UUID(parent_id_raw.strip())
            except ValueError as exc:
                raise ToolValidationError(
                    "Field 'parent_id' must be a valid UUID string"
                ) from exc

    memory_service = MemoryService(MemoryRepository())
    try:
        async with AsyncSessionLocal() as db:
            node = await memory_service.update_memory(
                db,
                memory_id=node_id,
                updates=updates,
                embedding_service=get_embedding_service(),
                ignore_embedding_errors=False,
            )
    except Exception as exc:  # noqa: BLE001
        _raise_memory_tool_validation_error(
            exc,
            not_found_detail="Memory node not found",
            parent_not_found_detail="Parent memory node not found",
        )
        raise

    return {
        **_memory_as_dict(node),
    }


async def handle_touch(payload: dict[str, Any]) -> dict[str, Any]:
    node_id_raw = payload.get("id")
    if not isinstance(node_id_raw, str) or not node_id_raw.strip():
        raise ToolValidationError("Field 'id' must be a non-empty UUID string")
    try:
        node_id = UUID(node_id_raw.strip())
    except ValueError as exc:
        raise ToolValidationError("Field 'id' must be a valid UUID string") from exc
    memory_service = MemoryService(MemoryRepository())
    try:
        async with AsyncSessionLocal() as db:
            node = await memory_service.touch_memory(db, node_id)
    except Exception as exc:  # noqa: BLE001
        _raise_memory_tool_validation_error(exc, not_found_detail="Memory node not found")
        raise
    return {
        "id": str(node.id),
        "last_accessed_at": node.last_accessed_at.isoformat(),
    }


async def handle_move(payload: dict[str, Any]) -> dict[str, Any]:
    node_ids_raw = payload.get("node_ids")
    if not isinstance(node_ids_raw, list) or not node_ids_raw:
        raise ToolValidationError("Field 'node_ids' must be a non-empty array of UUID strings")

    node_ids: list[UUID] = []
    seen_ids: set[UUID] = set()
    for raw in node_ids_raw:
        if not isinstance(raw, str) or not raw.strip():
            raise ToolValidationError("Each node_id must be a non-empty UUID string")
        try:
            parsed = UUID(raw.strip())
        except ValueError as exc:
            raise ToolValidationError(f"Invalid node_id UUID: {raw}") from exc
        if parsed in seen_ids:
            continue
        seen_ids.add(parsed)
        node_ids.append(parsed)
    if not node_ids:
        raise ToolValidationError("Field 'node_ids' must contain at least one UUID")

    to_root = payload.get("to_root", False)
    if not isinstance(to_root, bool):
        raise ToolValidationError("Field 'to_root' must be a boolean")

    target_parent_id_raw = payload.get("target_parent_id")
    target_parent_id: UUID | None = None
    if target_parent_id_raw is not None:
        if not isinstance(target_parent_id_raw, str) or not target_parent_id_raw.strip():
            raise ToolValidationError("Field 'target_parent_id' must be a UUID string")
        try:
            target_parent_id = UUID(target_parent_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'target_parent_id' must be a valid UUID string") from exc

    if to_root and target_parent_id is not None:
        raise ToolValidationError("Provide either to_root=true or target_parent_id, not both")
    if not to_root and target_parent_id is None:
        raise ToolValidationError("Provide target_parent_id or set to_root=true")

    memory_service = MemoryService(MemoryRepository())
    try:
        async with AsyncSessionLocal() as db:
            moved = await memory_service.move_memories(
                db,
                node_ids=node_ids,
                target_parent_id=target_parent_id,
                to_root=to_root,
            )
    except Exception as exc:  # noqa: BLE001
        _raise_memory_tool_validation_error(
            exc,
            not_found_detail="Memory node not found",
            parent_not_found_detail="Parent memory node not found",
        )
        raise

    return {
        "moved_node_ids": [str(item.id) for item in moved],
        "target_parent_id": None if to_root else str(target_parent_id),
        "to_root": to_root,
        "moved_count": len(moved),
    }


async def handle_delete(payload: dict[str, Any]) -> dict[str, Any]:
    node_id_raw = payload.get("id")
    if not isinstance(node_id_raw, str) or not node_id_raw.strip():
        raise ToolValidationError("Field 'id' must be a non-empty UUID string")
    try:
        node_id = UUID(node_id_raw.strip())
    except ValueError as exc:
        raise ToolValidationError("Field 'id' must be a valid UUID string") from exc
    memory_service = MemoryService(MemoryRepository())
    try:
        async with AsyncSessionLocal() as db:
            await memory_service.delete_memory(db, node_id)
    except Exception as exc:  # noqa: BLE001
        _raise_memory_tool_validation_error(exc, not_found_detail="Memory node not found")
        raise
    return {
        "id": str(node_id),
        "deleted": True,
    }


async def handle_search(payload: dict[str, Any]) -> dict[str, Any]:
    memory_search_service = get_memory_search_service()
    if memory_search_service is None:
        raise ToolValidationError("Memory search service is not configured")

    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolValidationError("Field 'query' must be a non-empty string")

    category = payload.get("category")
    if category is not None:
        if not isinstance(category, str) or category not in _ALLOWED_MEMORY_CATEGORIES:
            raise ToolValidationError(
                "Field 'category' must be one of: core, preference, project, correction"
            )

    limit = payload.get("limit", 10)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ToolValidationError("Field 'limit' must be a positive integer")
    limit = min(limit, 200)

    root_id_raw = payload.get("root_id")
    root_id: UUID | None = None
    if root_id_raw is not None:
        if not isinstance(root_id_raw, str) or not root_id_raw.strip():
            raise ToolValidationError("Field 'root_id' must be a UUID string")
        try:
            root_id = UUID(root_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'root_id' must be a valid UUID string") from exc

    auto_expand = payload.get("auto_expand", True)
    if not isinstance(auto_expand, bool):
        raise ToolValidationError("Field 'auto_expand' must be a boolean")

    memory_service = MemoryService(MemoryRepository())
    async with AsyncSessionLocal() as db:
        result = await memory_service.search_memories(
            db,
            query=query.strip(),
            category=category,
            root_id=root_id,
            limit=limit,
            memory_search_service=memory_search_service,
        )
        expanded: list[Memory] = []
        if auto_expand:
            expanded = await memory_service.expand_branches(
                db, items=result.items, root_id=root_id
            )

    return {
        "items": [
            {
                **_memory_as_dict(item),
                "score": result.scores.get(item.id),
            }
            for item in result.items
        ],
        "expanded_items": [_memory_as_dict(item) for item in expanded],
        "total": result.total,
    }


def _memory_command(payload: dict[str, Any]) -> str:
    raw = payload.get("command")
    if not isinstance(raw, str) or not raw.strip():
        raise ToolValidationError("Field 'command' must be a non-empty string")
    normalized = raw.strip().lower()
    if normalized not in ALLOWED_MEMORY_COMMANDS:
        raise ToolValidationError(
            "Field 'command' must be one of: " + ", ".join(ALLOWED_MEMORY_COMMANDS)
        )
    return normalized


async def handle_run(payload: dict[str, Any]) -> dict[str, Any]:
    command = _memory_command(payload)
    if command == "store":
        return await handle_store(payload)
    if command == "roots":
        return await handle_roots(payload)
    if command == "tree":
        return await handle_tree(payload)
    if command == "get_node":
        return await handle_get_node(payload)
    if command == "list_children":
        return await handle_list_children(payload)
    if command == "update":
        return await handle_update(payload)
    if command == "touch":
        return await handle_touch(payload)
    if command == "move":
        return await handle_move(payload)
    if command == "delete":
        return await handle_delete(payload)
    return await handle_search(payload)
