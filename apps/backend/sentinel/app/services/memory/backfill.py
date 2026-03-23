from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Memory
from app.services.memory.embeddings import EmbeddingService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MemoryEmbeddingBackfillStats:
    scanned: int = 0
    embedded: int = 0
    failed: int = 0


async def run_memory_embedding_backfill(
    *,
    stop_event: asyncio.Event,
    db_factory: async_sessionmaker[AsyncSession],
    embedding_service: EmbeddingService,
    batch_size: int = 100,
    max_rows: int = 0,
) -> MemoryEmbeddingBackfillStats:
    """Backfill missing memory embeddings on startup.

    This worker is best-effort and safe to rerun. It only targets rows where
    embedding is NULL.
    """
    safe_batch_size = max(1, min(batch_size, 500))
    remaining = max_rows if max_rows > 0 else None
    failed_ids: set[UUID] = set()
    stats = MemoryEmbeddingBackfillStats()
    started_at = datetime.now(UTC)

    logger.info(
        "memory embedding backfill started (batch_size=%s, max_rows=%s)",
        safe_batch_size,
        max_rows,
    )

    try:
        while not stop_event.is_set():
            target = safe_batch_size if remaining is None else min(safe_batch_size, remaining)
            if target <= 0:
                break

            async with db_factory() as db:
                rows = await _load_missing_embedding_batch(
                    db=db, limit=target, excluded_ids=failed_ids
                )
                if not rows:
                    break

                stats.scanned += len(rows)
                assignments, failed_in_batch = await _embed_rows(
                    rows=rows,
                    embedding_service=embedding_service,
                )
                failed_ids.update(failed_in_batch)
                stats.failed += len(failed_in_batch)

                if not assignments:
                    logger.warning(
                        "memory embedding backfill: no successful embeddings in batch; stopping to avoid loop"
                    )
                    break

                for memory, vector in assignments:
                    memory.embedding = vector
                await db.commit()

                stats.embedded += len(assignments)
                if remaining is not None:
                    remaining -= len(assignments)
    except Exception:  # noqa: BLE001
        logger.exception("memory embedding backfill crashed")

    elapsed = (datetime.now(UTC) - started_at).total_seconds()
    logger.info(
        "memory embedding backfill finished (embedded=%s scanned=%s failed=%s elapsed=%.2fs)",
        stats.embedded,
        stats.scanned,
        stats.failed,
        elapsed,
    )
    return stats


async def _load_missing_embedding_batch(
    *,
    db: AsyncSession,
    limit: int,
    excluded_ids: set[UUID],
) -> list[Memory]:
    # Keep query simple/portable and filter exclusions in Python.
    result = await db.execute(
        select(Memory).where(Memory.embedding.is_(None)).order_by(Memory.created_at.asc())
    )
    rows = result.scalars().all()
    if excluded_ids:
        rows = [row for row in rows if row.id not in excluded_ids]
    return rows[:limit]


async def _embed_rows(
    *,
    rows: Sequence[Memory],
    embedding_service: EmbeddingService,
) -> tuple[list[tuple[Memory, list[float]]], set[UUID]]:
    failed_ids: set[UUID] = set()
    candidates: list[Memory] = []
    texts: list[str] = []

    for row in rows:
        text = (row.content or "").strip()
        if not text:
            failed_ids.add(row.id)
            continue
        candidates.append(row)
        texts.append(text)

    if not candidates:
        return [], failed_ids

    try:
        vectors = await embedding_service.embed_batch(texts)
        if len(vectors) != len(candidates):
            raise RuntimeError("embedding API returned mismatched batch length")
        return list(zip(candidates, vectors, strict=True)), failed_ids
    except Exception:  # noqa: BLE001
        # Fall back per row to salvage progress if one text poisons the batch.
        assignments: list[tuple[Memory, list[float]]] = []
        for memory, text in zip(candidates, texts, strict=True):
            try:
                vector = await embedding_service.embed(text)
            except Exception:  # noqa: BLE001
                failed_ids.add(memory.id)
                continue
            assignments.append((memory, vector))
        return assignments, failed_ids
