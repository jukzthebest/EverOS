"""Fallback LLM provider chain."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from everos.core.observability.logging import get_logger

from .protocol import ChatMessage, ChatResponse, LLMClient, LLMError

logger = get_logger(__name__)


class FallbackLLMProvider:
    """Try providers in order until one returns a response."""

    def __init__(self, providers: list[tuple[str, LLMClient]]) -> None:
        if not providers:
            raise ValueError("fallback provider chain is empty")
        self._providers = providers

    @property
    def provider_names(self) -> list[str]:
        return [name for name, _ in self._providers]

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
        errors: list[str] = []
        for index, (name, provider) in enumerate(self._providers):
            try:
                response = await provider.chat(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    **extra,
                )
                if index > 0:
                    logger.info("llm_fallback_succeeded", provider=name)
                return response
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                logger.warning("llm_provider_failed", provider=name, error=str(exc))
        raise LLMError("all LLM providers failed: " + " | ".join(errors))
