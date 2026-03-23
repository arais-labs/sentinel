from __future__ import annotations

import asyncio

from app.config import Settings
from app.services.memory.embeddings import EmbeddingService


def _run(coro):
    return asyncio.run(coro)


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("bad status")

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self):
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, *, json: dict, headers: dict):
        self.calls.append({"url": url, "json": json, "headers": headers})
        input_value = json.get("input")
        if isinstance(input_value, list):
            data = [{"embedding": [float(i)] * 1536} for i, _ in enumerate(input_value)]
            return _FakeResponse({"data": data})
        return _FakeResponse({"data": [{"embedding": [0.5] * 1536}]})


def test_embedding_service_embed_returns_1536_vector():
    fake = _FakeClient()
    service = EmbeddingService("k", client_factory=lambda: fake)
    vector = _run(service.embed("hello world"))

    assert len(vector) == 1536
    assert vector[0] == 0.5
    assert fake.calls[0]["url"].endswith("/embeddings")


def test_embedding_service_embed_batch_returns_multiple_vectors_with_chunking():
    fake = _FakeClient()
    service = EmbeddingService("k", client_factory=lambda: fake, batch_size=1)
    vectors = _run(service.embed_batch(["a", "b"]))

    assert len(vectors) == 2
    assert len(vectors[0]) == 1536
    assert len(vectors[1]) == 1536
    assert len(fake.calls) == 2


def test_config_reads_embedding_api_key_from_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embed-key-123")

    loaded = Settings(_env_file=None)
    assert loaded.embedding_api_key == "embed-key-123"
