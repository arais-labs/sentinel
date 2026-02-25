from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx


class EmbeddingService:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        batch_size: int = 100,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=30))
        self._batch_size = max(1, batch_size)

    async def embed(self, text: str) -> list[float]:
        vectors = await self.embed_batch([text])
        return vectors[0] if vectors else []

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        output: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            chunk = texts[start : start + self._batch_size]
            vectors = await self._embed_chunk(chunk)
            output.extend(vectors)
        return output

    async def _embed_chunk(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, Any] = {
            "model": self._model,
            "input": texts if len(texts) > 1 else texts[0],
        }

        async with self._client_factory() as client:
            response = await client.post(
                f"{self._base_url}/embeddings",
                json=payload,
                headers={
                    "authorization": f"Bearer {self._api_key}",
                    "content-type": "application/json",
                },
            )
        response.raise_for_status()
        data = response.json()

        rows = data.get("data")
        if not isinstance(rows, list):
            raise RuntimeError("Embedding response missing 'data' list")

        vectors: list[list[float]] = []
        for row in rows:
            embedding = row.get("embedding") if isinstance(row, dict) else None
            if not isinstance(embedding, list):
                raise RuntimeError("Embedding row missing 'embedding' vector")
            vectors.append([float(item) for item in embedding])
        return vectors
