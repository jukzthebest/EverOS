"""Language preference wrapper for extraction LLM calls."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from .protocol import ChatMessage, ChatResponse, LLMClient

_ZH_SYSTEM_PROMPT = """\
你正在为 EverOS 生成长期记忆 Markdown。
请使用简体中文输出所有自然语言字段，包括摘要、主题、事实、洞察、画像、证据说明和未来提醒。
保留代码、命令、路径、URL、ID、专有名词、配置键、原文引用和结构化字段名，不要翻译这些内容。
如果原始内容是英文，可以用中文概括；只有必要的短引用保留原文。"""
_ZH_USER_SUFFIX = """\

重要输出语言要求：最终返回中的所有自然语言内容必须使用简体中文，包括 JSON 字段值里的 \
summary、subject、episode、fact、foresight、evidence、profile、description、basis 等。\
代码、命令、路径、URL、ID、配置键和必要引用保持原样。"""

_EN_SYSTEM_PROMPT = """\
You are generating EverOS long-term memory Markdown.
Write all natural-language memory fields in English while preserving code,
commands, paths, URLs, ids, proper nouns, config keys, quotes, and schema fields."""


class LanguageLLMProvider:
    """Inject a language preference before delegating to an LLM provider."""

    def __init__(self, provider: LLMClient, language: Literal["auto", "zh", "en"]):
        self._provider = provider
        self._language = language

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> ChatResponse:
        return await self._provider.chat(
            self._with_language_prompt(messages),
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            **extra,
        )

    def _with_language_prompt(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        prompt = _prompt_for_language(self._language)
        if prompt is None:
            return messages
        return [
            ChatMessage(role="system", content=prompt),
            *self._with_user_suffix(messages),
        ]

    def _with_user_suffix(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        suffix = _user_suffix_for_language(self._language)
        if suffix is None:
            return messages
        updated = list(messages)
        for index in range(len(updated) - 1, -1, -1):
            message = updated[index]
            if message.role == "user":
                updated[index] = ChatMessage(
                    role=message.role,
                    content=f"{message.content.rstrip()}\n{suffix}",
                )
                return updated
        return updated


def _prompt_for_language(language: Literal["auto", "zh", "en"]) -> str | None:
    if language == "zh":
        return _ZH_SYSTEM_PROMPT
    if language == "en":
        return _EN_SYSTEM_PROMPT
    return None


def _user_suffix_for_language(language: Literal["auto", "zh", "en"]) -> str | None:
    if language == "zh":
        return _ZH_USER_SUFFIX
    return None
