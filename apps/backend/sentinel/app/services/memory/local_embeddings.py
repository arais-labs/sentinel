"""CPU embeddings. Only model assets are downloaded; memory text stays local."""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from pathlib import Path
from typing import Any

from app.services.memory.embeddings import EmbeddingService

logger = logging.getLogger(__name__)


class LocalEmbeddingService(EmbeddingService):
    model_name = "BAAI/bge-small-en-v1.5"
    # Includes pooling/chunking version: model changes must rebuild stored vectors.
    fingerprint = "local:BAAI/bge-small-en-v1.5:chunk1000-mean:v1"

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._model: Any = None
        self._lock = threading.Lock()
        self._retry_at = 0.0

    async def embed(self, text: str) -> list[float]:
        vectors = await self.embed_batch([text])
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_sync, texts)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        # Serialize initialization and inference across requests and event loops.
        with self._lock:
            if time.monotonic() < self._retry_at:
                raise RuntimeError("Local embedding model unavailable; retrying shortly")
            try:
                if self._model is None:
                    from fastembed import TextEmbedding

                    logger.info("Loading local memory embedding model %s", self.model_name)
                    self._model = TextEmbedding(
                        model_name=self.model_name,
                        cache_dir=str(self._cache_dir),
                        threads=2,
                        providers=["CPUExecutionProvider"],
                    )
                output = []
                for text in texts:
                    if not text.strip():
                        raise ValueError("Cannot embed empty memory text")
                    # Preserve later sections instead of truncating the whole memory
                    # to the model's first context window. Overlap retains boundaries.
                    chunks = [text[i : i + 1000] for i in range(0, len(text), 800)]
                    vectors = list(self._model.embed(chunks, batch_size=16))
                    pooled = [sum(values) / len(vectors) for values in zip(*vectors, strict=True)]
                    norm = math.sqrt(sum(float(value) ** 2 for value in pooled))
                    if not norm or not math.isfinite(norm):
                        raise RuntimeError("Local model produced an invalid embedding")
                    output.append([float(value) / norm for value in pooled])
                return output
            except Exception:
                self._retry_at = time.monotonic() + 60
                logger.exception(
                    "Local memory embeddings unavailable; keyword search remains available"
                )
                raise
