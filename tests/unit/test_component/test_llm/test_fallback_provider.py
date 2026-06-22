"""Fallback LLM provider chain."""

from __future__ import annotations

import pytest

from everos.component.llm.fallback_provider import FallbackLLMProvider
from everos.component.llm.protocol import ChatMessage, ChatResponse, LLMError


class _FakeProvider:
    def __init__(self, name: str, *, fail: bool) -> None:
        self.name = name
        self.fail = fail
        self.calls = 0

    async def chat(self, messages, **extra):
        del messages, extra
        self.calls += 1
        if self.fail:
            raise LLMError(f"{self.name} failed")
        return ChatResponse(content=self.name, model=self.name)


@pytest.mark.asyncio
async def test_fallback_uses_first_successful_provider() -> None:
    first = _FakeProvider("grok", fail=True)
    second = _FakeProvider("codex", fail=False)
    third = _FakeProvider("openai", fail=False)
    chain = FallbackLLMProvider(
        [("grok_oauth", first), ("codex_oauth", second), ("openai", third)]
    )

    response = await chain.chat([ChatMessage(role="user", content="hello")])

    assert response.content == "codex"
    assert first.calls == 1
    assert second.calls == 1
    assert third.calls == 0


@pytest.mark.asyncio
async def test_fallback_raises_combined_error_when_all_fail() -> None:
    chain = FallbackLLMProvider(
        [
            ("grok_oauth", _FakeProvider("grok", fail=True)),
            ("codex_oauth", _FakeProvider("codex", fail=True)),
        ]
    )

    with pytest.raises(LLMError, match="all LLM providers failed"):
        await chain.chat([ChatMessage(role="user", content="hello")])
