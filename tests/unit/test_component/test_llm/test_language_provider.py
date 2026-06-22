"""Language preference LLM wrapper."""

from __future__ import annotations

import pytest

from everos.component.llm.language_provider import LanguageLLMProvider
from everos.component.llm.protocol import ChatMessage, ChatResponse


class _CaptureProvider:
    def __init__(self) -> None:
        self.messages: list[ChatMessage] | None = None

    async def chat(self, messages, **extra):
        del extra
        self.messages = messages
        return ChatResponse(content="ok", model="fake")


@pytest.mark.asyncio
async def test_zh_language_provider_injects_system_prompt() -> None:
    base = _CaptureProvider()
    wrapped = LanguageLLMProvider(base, "zh")

    await wrapped.chat([ChatMessage(role="user", content="hello")])

    assert base.messages is not None
    assert base.messages[0].role == "system"
    assert "简体中文" in base.messages[0].content
    assert base.messages[1].content.startswith("hello")
    assert "JSON 字段值" in base.messages[1].content


@pytest.mark.asyncio
async def test_auto_language_provider_is_passthrough() -> None:
    base = _CaptureProvider()
    wrapped = LanguageLLMProvider(base, "auto")
    messages = [ChatMessage(role="user", content="hello")]

    await wrapped.chat(messages)

    assert base.messages is messages
