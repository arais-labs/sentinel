from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import Memory, Session
from app.services.memory.search import MemorySearchResult, MemorySearchService
from tests.fake_db import FakeDB


class _FakeEmbeddingService:
    def __init__(self, vector: list[float] | None = None):
        self.vector = vector or [1.0, 0.0]
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return list(self.vector)


class _FakeMemorySearchService:
    def __init__(self, results: list[MemorySearchResult]):
        self._results = results
        self.calls: list[dict] = []

    async def search(self, db, query: str, *, category: str | None = None, limit: int = 10):
        self.calls.append({"query": query, "category": category, "limit": limit})
        return self._results[:limit]


def test_memory_store_auto_embeds_when_embedding_service_available():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    old_embedding = getattr(app.state, "embedding_service", None)
    old_search = getattr(app.state, "memory_search_service", None)
    app_main.init_db = _noop_init_db
    app.state.embedding_service = _FakeEmbeddingService([0.1, 0.2])
    app.state.memory_search_service = None
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/api/v1/memory",
            json={"content": "embed me", "category": "project", "metadata": {}},
            headers=headers,
        )
        assert resp.status_code == 200

        stored = fake_db.storage[Memory][0]
        assert stored.embedding == [0.1, 0.2]
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
        app.state.embedding_service = old_embedding
        app.state.memory_search_service = old_search


def test_memory_store_works_without_embedding_service():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    old_embedding = getattr(app.state, "embedding_service", None)
    old_search = getattr(app.state, "memory_search_service", None)
    app_main.init_db = _noop_init_db
    app.state.embedding_service = None
    app.state.memory_search_service = None
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/api/v1/memory",
            json={"content": "no embedding", "category": "project", "metadata": {}},
            headers=headers,
        )
        assert resp.status_code == 200
        assert fake_db.storage[Memory][0].embedding is None
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
        app.state.embedding_service = old_embedding
        app.state.memory_search_service = old_search


def test_memory_list_uses_hybrid_search_service_when_available():
    fake_db = FakeDB()
    memory = Memory(content="Matched memory", category="project", metadata_json={})
    fake_db.add(memory)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    old_search = getattr(app.state, "memory_search_service", None)
    app_main.init_db = _noop_init_db
    search_service = _FakeMemorySearchService([MemorySearchResult(memory=memory, score=0.9)])
    app.state.memory_search_service = search_service
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/api/v1/memory?query=matched", headers=headers)
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["items"][0]["id"] == str(memory.id)
        assert payload["items"][0]["score"] == 0.9
        assert search_service.calls[0]["query"] == "matched"
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
        app.state.memory_search_service = old_search


def test_memory_list_falls_back_to_substring_when_no_embedding_service():
    db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="m")
    db.add(session)
    db.add(Memory(content="alpha bravo", category="project", metadata_json={}))
    db.add(Memory(content="charlie delta", category="project", metadata_json={}))

    search = MemorySearchService(embedding_service=None)
    results = _run(search.search(db, "alpha", category="project", limit=10))
    assert len(results) == 1
    assert results[0].memory.content == "alpha bravo"


def test_memory_search_returns_recent_when_no_match_and_no_embedding():
    db = FakeDB()
    db.add(Memory(content="first memory", category="project", metadata_json={}))
    db.add(Memory(content="second memory", category="project", metadata_json={}))

    search = MemorySearchService(embedding_service=None)
    results = _run(search.search(db, "what memories do you have", category="project", limit=10))
    assert len(results) == 2
    assert results[0].memory.content in {"first memory", "second memory"}


def test_memory_search_vector_order_and_rrf_merge():
    db = FakeDB()
    m1 = Memory(content="apple project", category="project", metadata_json={}, embedding=[1.0, 0.0])
    m2 = Memory(content="apple docs", category="project", metadata_json={}, embedding=[0.2, 0.8])
    m3 = Memory(content="banana notes", category="project", metadata_json={}, embedding=[0.0, 1.0])
    db.add(m1)
    db.add(m2)
    db.add(m3)

    search = MemorySearchService(embedding_service=_FakeEmbeddingService([1.0, 0.0]))

    vector = _run(search._vector_search(db, [1.0, 0.0], "project", 3))
    assert vector[0][0].id == m1.id

    keyword = _run(search._keyword_search(db, "apple", "project", 3))
    assert keyword[0][0].id in {m1.id, m2.id}

    merged = search._rrf_merge(vector, keyword, k=60)
    assert merged
    assert merged[0].memory.id in {m1.id, m2.id}


def _run(coro):
    import asyncio

    return asyncio.run(coro)
