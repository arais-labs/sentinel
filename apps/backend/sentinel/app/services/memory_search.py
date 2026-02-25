from __future__ import annotations

import math
from datetime import UTC, datetime
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Memory
from app.services.embeddings import EmbeddingService


@dataclass(slots=True)
class MemorySearchResult:
    memory: Memory
    score: float


class MemorySearchService:
    def __init__(self, embedding_service: EmbeddingService | None = None) -> None:
        self._embedding_service = embedding_service

    async def search(
        self,
        db: AsyncSession,
        query: str,
        *,
        category: str | None = None,
        limit: int = 10,
    ) -> list[MemorySearchResult]:
        query_text = query.strip()
        if not query_text:
            return []

        safe_limit = max(1, min(limit, 100))

        if self._embedding_service is None:
            keyword_results = await self._keyword_search(db, query_text, category, safe_limit * 2)
            if keyword_results:
                return [MemorySearchResult(memory=memory, score=score) for memory, score in keyword_results[:safe_limit]]

            fallback = await self._substring_fallback(db, query_text, category, safe_limit)
            if fallback:
                return [MemorySearchResult(memory=memory, score=score) for memory, score in fallback]

            recent = await self._recent_fallback(db, category, safe_limit)
            return [MemorySearchResult(memory=memory, score=score) for memory, score in recent]

        try:
            query_embedding = await self._embedding_service.embed(query_text)
        except Exception:  # noqa: BLE001 - embedding service may be misconfigured
            query_embedding = []

        if query_embedding:
            vector_results = await self._vector_search(db, query_embedding, category, safe_limit * 2)
        else:
            vector_results = []
        keyword_results = await self._keyword_search(db, query_text, category, safe_limit * 2)

        merged = self._rrf_merge(vector_results, keyword_results, k=60)
        if merged:
            return merged[:safe_limit]

        fallback = await self._substring_fallback(db, query_text, category, safe_limit)
        if fallback:
            return [MemorySearchResult(memory=memory, score=score) for memory, score in fallback]

        recent = await self._recent_fallback(db, category, safe_limit)
        return [MemorySearchResult(memory=memory, score=score) for memory, score in recent]

    async def _vector_search(
        self,
        db: AsyncSession,
        query_embedding: list[float],
        category: str | None,
        limit: int,
    ) -> list[tuple[Memory, float]]:
        if not query_embedding:
            return []

        try:
            distance_expr = Memory.embedding.cosine_distance(query_embedding)
            score_expr = (1 - distance_expr).label("score")
            stmt = select(Memory, score_expr).where(Memory.embedding.is_not(None))
            if category:
                stmt = stmt.where(Memory.category == category)
            stmt = stmt.order_by(distance_expr).limit(limit)
            result = await db.execute(stmt)
            rows = result.all()
            parsed: list[tuple[Memory, float]] = []
            for row in rows:
                memory = row[0]
                score = float(row[1])
                parsed.append((memory, score))
            if parsed:
                return parsed
        except Exception:  # noqa: BLE001 - fallback for non-Postgres/fake sessions
            pass

        result = await db.execute(select(Memory))
        memories = result.scalars().all()
        filtered: list[tuple[Memory, float]] = []
        for memory in memories:
            if category and memory.category != category:
                continue
            if not memory.embedding:
                continue
            score = _cosine_similarity(query_embedding, memory.embedding)
            filtered.append((memory, score))
        filtered.sort(key=lambda item: item[1], reverse=True)
        return filtered[:limit]

    async def _keyword_search(
        self,
        db: AsyncSession,
        query: str,
        category: str | None,
        limit: int,
    ) -> list[tuple[Memory, float]]:
        try:
            query_expr = func.plainto_tsquery("english", query)
            combined_text = func.concat_ws(
                " ",
                func.coalesce(Memory.title, ""),
                func.coalesce(Memory.summary, ""),
                Memory.content,
            )
            rank_expr = func.ts_rank(func.to_tsvector("english", combined_text), query_expr).label("score")
            stmt = select(Memory, rank_expr).where(func.to_tsvector("english", combined_text).op("@@")(query_expr))
            if category:
                stmt = stmt.where(Memory.category == category)
            stmt = stmt.order_by(rank_expr.desc()).limit(limit)
            result = await db.execute(stmt)
            rows = result.all()
            parsed: list[tuple[Memory, float]] = []
            for row in rows:
                memory = row[0]
                score = float(row[1])
                parsed.append((memory, score))
            if parsed:
                return parsed
        except Exception:  # noqa: BLE001 - fallback for non-Postgres/fake sessions
            pass

        result = await db.execute(select(Memory))
        memories = result.scalars().all()
        terms = [part.strip().lower() for part in query.split() if part.strip()]
        ranked: list[tuple[Memory, float]] = []
        for memory in memories:
            if category and memory.category != category:
                continue
            text = " ".join(part for part in [memory.title or "", memory.summary or "", memory.content] if part).lower()
            score = float(sum(text.count(term) for term in terms))
            if score > 0:
                ranked.append((memory, score))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked[:limit]

    async def _substring_fallback(
        self,
        db: AsyncSession,
        query: str,
        category: str | None,
        limit: int,
    ) -> list[tuple[Memory, float]]:
        result = await db.execute(select(Memory))
        memories = result.scalars().all()
        lowered = query.lower()
        matched: list[tuple[Memory, float]] = []
        for memory in memories:
            if category and memory.category != category:
                continue
            combined = " ".join(
                part for part in [memory.title or "", memory.summary or "", memory.content] if part
            ).lower()
            if lowered in combined:
                matched.append((memory, 1.0))
        matched.sort(key=lambda item: item[0].created_at, reverse=True)
        return matched[:limit]

    async def _recent_fallback(
        self,
        db: AsyncSession,
        category: str | None,
        limit: int,
    ) -> list[tuple[Memory, float]]:
        result = await db.execute(select(Memory))
        memories = result.scalars().all()
        if category:
            memories = [memory for memory in memories if memory.category == category]
        memories.sort(key=lambda item: item.created_at, reverse=True)
        return [(memory, 0.0) for memory in memories[:limit]]

    def _rrf_merge(
        self,
        vector_results: list[tuple[Memory, float]],
        keyword_results: list[tuple[Memory, float]],
        *,
        k: int = 60,
    ) -> list[MemorySearchResult]:
        score_by_id: dict[Any, float] = {}
        memory_by_id: dict[Any, Memory] = {}

        for rank, (memory, _score) in enumerate(vector_results, start=1):
            memory_by_id[memory.id] = memory
            score_by_id[memory.id] = score_by_id.get(memory.id, 0.0) + (1.0 / (k + rank))

        for rank, (memory, _score) in enumerate(keyword_results, start=1):
            memory_by_id[memory.id] = memory
            score_by_id[memory.id] = score_by_id.get(memory.id, 0.0) + (1.0 / (k + rank))

        merged = [
            MemorySearchResult(memory=memory_by_id[memory_id], score=score)
            for memory_id, score in score_by_id.items()
        ]
        merged.sort(key=lambda item: item.score, reverse=True)
        return merged

    async def list_roots(self, db: AsyncSession) -> list[Memory]:
        result = await db.execute(select(Memory))
        memories = result.scalars().all()
        roots = [item for item in memories if item.parent_id is None]
        min_time = datetime.min.replace(tzinfo=UTC)
        roots.sort(
            key=lambda item: (
                bool(item.pinned),
                int(item.importance or 0),
                item.last_accessed_at or item.updated_at or item.created_at or min_time,
            ),
            reverse=True,
        )
        return roots


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
