"""Grok CLI OAuth provider.

Uses the same auth material and chat proxy documented by the local Grok
CLI: ``~/.grok/auth.json`` and ``https://cli-chat-proxy.grok.com/v1``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import httpx

from .oauth_auth import load_bearer_auth
from .protocol import ChatMessage, ChatResponse, LLMError, Usage

DEFAULT_GROK_AUTH_FILE = Path("~/.grok/auth.json").expanduser()
DEFAULT_GROK_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
DEFAULT_GROK_VERSION_FILE = Path("~/.grok/version.json").expanduser()


class GrokOAuthProvider:
    """Grok provider using the local terminal Grok OAuth session."""

    def __init__(
        self,
        *,
        model: str,
        auth_file: Path | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> None:
        self._model = model
        self._auth_file = auth_file or DEFAULT_GROK_AUTH_FILE
        self._base_url = (base_url or DEFAULT_GROK_BASE_URL).rstrip("/")
        self._timeout = timeout
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._client_version = _load_grok_client_version()

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
        del extra
        effective_model = model or self._model
        body: dict[str, Any] = {
            "model": effective_model,
            "messages": [m.model_dump() for m in messages],
            "stream": True,
            "temperature": (
                temperature if temperature is not None else self._temperature
            ),
        }
        effective_max = max_tokens if max_tokens is not None else self._max_tokens
        if effective_max is not None:
            body["max_tokens"] = effective_max
        if response_format is not None:
            body["response_format"] = dict(response_format)

        text_parts: list[str] = []
        usage: Usage | None = None
        try:
            async for event in self._stream_events(body, effective_model):
                if "error" in event:
                    raise LLMError(json.dumps(event, ensure_ascii=False))
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    message = choice.get("message") or {}
                    content = delta.get("content") or message.get("content")
                    if content:
                        text_parts.append(str(content))
                usage = _usage_from_event(event) or usage
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(str(exc)) from exc

        return ChatResponse(
            content="".join(text_parts),
            model=effective_model,
            usage=usage,
            finish_reason="stop",
            raw=None,
        )

    async def _stream_events(
        self, body: dict[str, Any], model: str
    ) -> AsyncIterator[dict[str, Any]]:
        access_token, _ = load_bearer_auth(self._auth_file)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-XAI-Token-Auth": "xai-grok-cli",
            "User-Agent": f"grok/{self._client_version}",
            "x-grok-model-override": model,
            "x-grok-client-version": self._client_version,
        }
        timeout = httpx.Timeout(self._timeout, connect=min(self._timeout, 20.0))
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=body,
            ) as response,
        ):
            if response.status_code >= 400:
                text = await response.aread()
                raise LLMError(
                    f"grok chat proxy HTTP {response.status_code}: "
                    f"{text.decode(errors='replace')}"
                )
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if not data or data == "[DONE]":
                    continue
                yield json.loads(data)


def _usage_from_event(event: dict[str, Any]) -> Usage | None:
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None
    return Usage(
        prompt_tokens=usage.get("prompt_tokens") or usage.get("input_tokens"),
        completion_tokens=usage.get("completion_tokens")
        or usage.get("output_tokens"),
    )


def _load_grok_client_version(
    version_file: Path = DEFAULT_GROK_VERSION_FILE,
) -> str:
    try:
        data = json.loads(version_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "0.2.60"
    version = data.get("version")
    return version if isinstance(version, str) and version else "0.2.60"
