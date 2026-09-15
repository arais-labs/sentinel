from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from sqlite_vec import serialize_float32
from sqlalchemy import and_, case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Memory
from app.services.memory.embeddings import EmbeddingService


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
                return [
                    MemorySearchResult(memory=memory, score=score)
                    for memory, score in keyword_results[:safe_limit]
                ]

            fallback = await self._substring_fallback(db, query_text, category, safe_limit)
            if fallback:
                return [
                    MemorySearchResult(memory=memory, score=score) for memory, score in fallback
                ]

            recent = await self._recent_fallback(db, category, safe_limit)
            return [MemorySearchResult(memory=memory, score=score) for memory, score in recent]

        try:
            query_embedding = await self._embedding_service.embed(query_text)
        except Exception:  # noqa: BLE001 - embedding service may be misconfigured
            query_embedding = []

        if query_embedding:
            vector_results = await self._vector_search(
                db, query_embedding, category, safe_limit * 2
            )
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
        if (
            not query_embedding
            or not all(math.isfinite(x) for x in query_embedding)
            or not any(query_embedding)
        ):
            return []

        compatible = [
            Memory.embedding.is_not(None),
            func.vec_length(Memory.embedding) == len(query_embedding),
        ]
        fingerprint = getattr(self._embedding_service, "fingerprint", None)
        if fingerprint:
            compatible.append(Memory.metadata_json["_embedding_model"].as_string() == fingerprint)
        # CASE guards distance evaluation even if SQLite reorders WHERE clauses.
        distance = case(
            (
                and_(*compatible),
                func.vec_distance_cosine(Memory.embedding, serialize_float32(query_embedding)),
            ),
            else_=None,
        )
        stmt = select(Memory, (1 - distance).label("score")).where(distance.is_not(None))
        if category:
            stmt = stmt.where(Memory.category == category)
        result = await db.execute(stmt.order_by(distance, Memory.id).limit(limit))
        return [(memory, float(score)) for memory, score in result.all()]

    async def _keyword_search(
        self,
        db: AsyncSession,
        query: str,
        category: str | None,
        limit: int,
    ) -> list[tuple[Memory, float]]:
        terms = re.findall(r"\w+", query, flags=re.UNICODE)
        if not terms:
            return []
        # Treat input as words, never as FTS operators or column expressions.
        match = " AND ".join('"' + term + '"' for term in terms)
        stmt = select(Memory, text("-bm25(memories_fts, 3.0, 2.0, 1.0) AS score")).from_statement(
            text(
                "SELECT memories.*, -bm25(memories_fts, 3.0, 2.0, 1.0) AS score "
                "FROM memories JOIN memories_fts ON memories.rowid = memories_fts.rowid "
                "WHERE memories_fts MATCH :query "
                "AND (:category IS NULL OR memories.category = :category) "
                "ORDER BY bm25(memories_fts, 3.0, 2.0, 1.0), memories.id LIMIT :limit"
            )
        )
        result = await db.execute(stmt, {"query": match, "category": category, "limit": limit})
        return [(memory, float(score)) for memory, score in result.all()]

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
