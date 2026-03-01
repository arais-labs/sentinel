from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.models import Memory

MIN_TIME = datetime.min.replace(tzinfo=UTC)


def children_map(memories: list[Memory]) -> dict[UUID | None, list[Memory]]:
    mapping: dict[UUID | None, list[Memory]] = {}
    for memory in memories:
        mapping.setdefault(memory.parent_id, []).append(memory)
    return mapping


def descendant_ids(mapping: dict[UUID | None, list[Memory]], root_id: UUID) -> set[UUID]:
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


def is_descendant(*, target_parent_id: UUID, node_id: UUID, memories: list[Memory]) -> bool:
    mapping = children_map(memories)
    return node_id in descendant_ids(mapping, target_parent_id)


def filter_by_root(items: list[Memory], memories: list[Memory], root_id: UUID) -> list[Memory]:
    mapping = children_map(memories)
    allowed = descendant_ids(mapping, root_id)
    allowed.add(root_id)
    return [item for item in items if item.id in allowed]


def expand_memory_branches(items: list[Memory], memories: list[Memory]) -> list[Memory]:
    by_id = {item.id: item for item in memories}
    children = children_map(memories)
    expanded: list[Memory] = []
    seen: set[UUID] = set()
    for item in items:
        if item.id not in seen:
            seen.add(item.id)
            expanded.append(item)

        # Include lineage to root.
        current = item
        lineage: list[Memory] = []
        guard: set[UUID] = set()
        while current.parent_id and current.parent_id in by_id and current.parent_id not in guard:
            guard.add(current.parent_id)
            current = by_id[current.parent_id]
            lineage.append(current)
        for node in reversed(lineage):
            if node.id not in seen:
                seen.add(node.id)
                expanded.append(node)

        # Include direct children for quick drill-down.
        for child in children.get(item.id, []):
            if child.id in seen:
                continue
            seen.add(child.id)
            expanded.append(child)
    return expanded
