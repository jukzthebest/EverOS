"""Deterministic local hashing embedding provider.

This is a bootstrap provider for local-first installs without an embeddings
API key. It is not a semantic model; it creates stable sparse vectors from
token hashes so LanceDB can be built and keyword/BM25-heavy retrieval can run
fully offline.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class LocalHashEmbeddingProvider:
    """Offline deterministic embedding provider."""

    def __init__(self, *, dim: int = 1024) -> None:
        self.dim = dim

    async def embed(self, text: str) -> list[float]:
        return self._embed_sync(text)

    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_sync(text) for text in texts]

    def _embed_sync(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = _TOKEN_RE.findall(text.lower())
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dim
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]
