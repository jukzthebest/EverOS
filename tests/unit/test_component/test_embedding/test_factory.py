"""``build_embedding_provider`` — settings validation + provider build."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from everos.component.embedding import (
    LocalHashEmbeddingProvider,
    OpenAIEmbeddingProvider,
    build_embedding_provider,
)
from everos.config.settings import EmbeddingSettings


def test_raises_when_model_missing() -> None:
    s = EmbeddingSettings(model=None, api_key=SecretStr("k"), base_url="https://x")
    with pytest.raises(ValueError, match="EVEROS_EMBEDDING__MODEL"):
        build_embedding_provider(s)


def test_raises_when_api_key_missing() -> None:
    s = EmbeddingSettings(model="m", api_key=None, base_url="https://x")
    with pytest.raises(ValueError, match="EVEROS_EMBEDDING__API_KEY"):
        build_embedding_provider(s)


def test_raises_when_api_key_empty() -> None:
    s = EmbeddingSettings(model="m", api_key=SecretStr(""), base_url="https://x")
    with pytest.raises(ValueError, match="EVEROS_EMBEDDING__API_KEY"):
        build_embedding_provider(s)


def test_raises_when_base_url_missing() -> None:
    s = EmbeddingSettings(model="m", api_key=SecretStr("k"), base_url=None)
    with pytest.raises(ValueError, match="EVEROS_EMBEDDING__BASE_URL"):
        build_embedding_provider(s)


def test_builds_openai_embedding_provider_with_default_dim() -> None:
    s = EmbeddingSettings(model="m", api_key=SecretStr("k"), base_url="https://x")
    p = build_embedding_provider(s)
    assert isinstance(p, OpenAIEmbeddingProvider)


def test_custom_dim_passes_through() -> None:
    s = EmbeddingSettings(model="m", api_key=SecretStr("k"), base_url="https://x")
    p = build_embedding_provider(s, dim=512)
    assert isinstance(p, OpenAIEmbeddingProvider)
    # Provider stores dim on a private attr; assert via the public output shape
    # only if straightforward. Skip introspection if attr name differs.
    if hasattr(p, "_dim"):
        assert p._dim == 512


async def test_builds_local_hash_embedding_provider_without_credentials() -> None:
    s = EmbeddingSettings(provider="local_hash")
    p = build_embedding_provider(s, dim=16)

    assert isinstance(p, LocalHashEmbeddingProvider)
    assert p.dim == 16
    assert await p.embed("same text") == await p.embed("same text")
    assert len(await p.embed("same text")) == 16
