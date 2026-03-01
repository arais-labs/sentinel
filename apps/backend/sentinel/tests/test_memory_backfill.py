from __future__ import annotations

import asyncio

from app.models import Memory
from app.services.memory.backfill import run_memory_embedding_backfill
from tests.fake_db import FakeDB


class _SessionCtx:
    def __init__(self, db: FakeDB):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, db: FakeDB):
        self._db = db

    def __call__(self):
        return _SessionCtx(self._db)


class _BatchEmbeddingService:
    def __init__(self):
        self.batch_calls = 0
        self.single_calls = 0

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls += 1
        return [[float(len(text))] for text in texts]

    async def embed(self, text: str) -> list[float]:
        self.single_calls += 1
        return [float(len(text))]


class _FallbackEmbeddingService:
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        _ = texts
        raise RuntimeError("batch failure")

    async def embed(self, text: str) -> list[float]:
        if text == "bad":
            raise RuntimeError("single failure")
        return [1.0]


def _run(coro):
    return asyncio.run(coro)


def test_memory_embedding_backfill_embeds_missing_rows():
    db = FakeDB()
    missing_one = Memory(content="hello", category="project", metadata_json={})
    missing_two = Memory(content="world", category="project", metadata_json={})
    existing = Memory(content="already", category="project", metadata_json={}, embedding=[9.0])
    db.add(missing_one)
    db.add(missing_two)
    db.add(existing)

    stop_event = asyncio.Event()
    service = _BatchEmbeddingService()
    stats = _run(
        run_memory_embedding_backfill(
            stop_event=stop_event,
            db_factory=_SessionFactory(db),
            embedding_service=service,
            batch_size=10,
        )
    )

    assert stats.embedded == 2
    assert stats.failed == 0
    assert missing_one.embedding == [5.0]
    assert missing_two.embedding == [5.0]
    assert existing.embedding == [9.0]
    assert service.batch_calls >= 1
    assert service.single_calls == 0


def test_memory_embedding_backfill_falls_back_to_single_embed_and_skips_failures():
    db = FakeDB()
    bad = Memory(content="bad", category="project", metadata_json={})
    good = Memory(content="good", category="project", metadata_json={})
    db.add(bad)
    db.add(good)

    stop_event = asyncio.Event()
    stats = _run(
        run_memory_embedding_backfill(
            stop_event=stop_event,
            db_factory=_SessionFactory(db),
            embedding_service=_FallbackEmbeddingService(),
            batch_size=10,
        )
    )

    assert stats.embedded == 1
    assert stats.failed >= 1
    assert bad.embedding is None
    assert good.embedding == [1.0]
