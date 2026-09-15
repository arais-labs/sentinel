import asyncio
import math
import sys
import threading
import types

import pytest

from app.services.memory.local_embeddings import LocalEmbeddingService


@pytest.mark.asyncio
async def test_local_model_is_lazy_serialized_and_runs_off_event_loop(tmp_path, monkeypatch):
    main_thread = threading.get_ident()
    constructions = []
    chunks_seen = []

    class Model:
        def __init__(self, **kwargs):
            assert threading.get_ident() != main_thread
            constructions.append(kwargs)

        def embed(self, chunks, **kwargs):
            assert threading.get_ident() != main_thread
            chunks_seen.extend(chunks)
            return [[3.0, 4.0] for _ in chunks]

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=Model))
    service = LocalEmbeddingService(tmp_path)
    assert not constructions
    values = await asyncio.gather(service.embed("a" * 1800 + "tail"), service.embed("query"))
    assert len(constructions) == 1
    assert constructions[0]["providers"] == ["CPUExecutionProvider"]
    assert any(chunk.endswith("tail") for chunk in chunks_seen)
    assert all(len(chunk) <= 1000 for chunk in chunks_seen)
    assert all(math.isclose(sum(x * x for x in vector), 1.0) for vector in values)


@pytest.mark.asyncio
async def test_failed_download_has_retry_cooldown(tmp_path, monkeypatch):
    calls = []

    def unavailable(**kwargs):
        calls.append(kwargs)
        raise OSError("offline")

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=unavailable))
    service = LocalEmbeddingService(tmp_path)
    with pytest.raises(OSError):
        await service.embed("query")
    with pytest.raises(RuntimeError, match="retrying shortly"):
        await service.embed("query")
    assert len(calls) == 1
