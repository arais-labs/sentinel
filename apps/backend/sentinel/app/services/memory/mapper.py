from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.models import Memory
from app.schemas.memory import MemoryResponse
from app.services.memory.search import MemorySearchResult


def memory_to_response(memory: Memory, *, score: float | None = None) -> MemoryResponse:
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


def score_map(results: Sequence[MemorySearchResult]) -> dict[UUID, float]:
    return {item.memory.id: item.score for item in results}
