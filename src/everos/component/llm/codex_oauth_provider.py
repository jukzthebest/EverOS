"""Codex ChatGPT OAuth provider.

This adapter reuses the local Codex ChatGPT login stored in
``~/.codex/auth.json`` and speaks the Codex Responses WebSocket protocol
used by the official Codex client. It is intentionally scoped to EverOS'
LLM extraction use case: text messages in, streamed text out.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import websockets

from .oauth_auth import load_bearer_auth
from .protocol import ChatMessage, ChatResponse, LLMError, Usage

DEFAULT_CODEX_AUTH_FILE = Path("~/.codex/auth.json").expanduser()
DEFAULT_CODEX_RESPONSES_WS_URL = "wss://chatgpt.com/backend-api/codex/responses"


class CodexOAuthProvider:
    """LLM provider backed by the local Codex ChatGPT OAuth session."""

    def __init__(
        self,
        *,
        model: str,
        auth_file: Path | None = None,
        base_url: str | None = None,
        service_tier: str | None = "priority",
        originator: str = "codex_cli_rs",
        user_agent: str = "codex-cli/0.141.0",
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._auth_file = auth_file or DEFAULT_CODEX_AUTH_FILE
        self._url = base_url or DEFAULT_CODEX_RESPONSES_WS_URL
        self._service_tier = service_tier
        self._originator = originator
        self._user_agent = user_agent
        self._timeout = timeout

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
        """Send a Codex Responses WebSocket request and collect text deltas."""
        del temperature, max_tokens, extra
        request = self._build_request(
            model=model or self._model,
            messages=messages,
            response_format=response_format,
        )
        headers = self._headers()

        text_parts: list[str] = []
        usage: Usage | None = None
        finish_reason: str | None = None
        raw_completed: dict[str, Any] | None = None
        try:
            async with websockets.connect(
                self._url,
                additional_headers=headers,
                compression="deflate",
                open_timeout=min(self._timeout, 20.0),
                ping_interval=None,
                max_size=8 * 1024 * 1024,
            ) as ws:
                await ws.send(json.dumps(request))
                async for raw in ws:
                    event = json.loads(raw)
                    event_type = event.get("type")
                    if event_type == "error":
                        raise LLMError(json.dumps(event, ensure_ascii=False))
                    if event_type == "response.output_text.delta":
                        text_parts.append(str(event.get("delta") or ""))
                    elif event_type == "response.completed":
                        raw_completed = event
                        usage = _usage_from_completed(event)
                        finish_reason = "stop"
                        break
                    elif event_type in {"response.failed", "response.incomplete"}:
                        raise LLMError(json.dumps(event, ensure_ascii=False))
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(str(exc)) from exc

        return ChatResponse(
            content="".join(text_parts),
            model=model or self._model,
            usage=usage,
            finish_reason=_normalise_finish_reason(finish_reason),
            raw=raw_completed,
        )

    def _headers(self) -> dict[str, str]:
        access_token, account_id = load_bearer_auth(self._auth_file)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "OpenAI-Beta": "responses_websockets=2026-02-06",
            "originator": self._originator,
            "User-Agent": self._user_agent,
            "x-client-request-id": str(uuid.uuid4()),
        }
        if account_id:
            headers["chatgpt-account-id"] = account_id
        return headers

    def _build_request(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        response_format: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        instructions, input_items = _messages_to_responses_input(messages)
        if response_format is not None:
            instructions = _append_response_format_instruction(
                instructions, response_format
            )

        payload: dict[str, Any] = {
            "type": "response.create",
            "model": model,
            "instructions": instructions
            or "You are a concise assistant. Follow the user request exactly.",
            "input": input_items,
            "tools": [],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "reasoning": None,
            "store": False,
            "stream": True,
            "include": [],
        }
        if self._service_tier:
            payload["service_tier"] = self._service_tier
        return payload


def _messages_to_responses_input(
    messages: list[ChatMessage],
) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []
    for message in messages:
        text = _content_to_text(message.content)
        if message.role == "system":
            instructions.append(text)
            continue
        input_items.append(
            {
                "type": "message",
                "role": message.role,
                "content": [{"type": "input_text", "text": text}],
            }
        )
    return "\n\n".join(part for part in instructions if part), input_items


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if hasattr(part, "text"):
                parts.append(str(part.text))
            elif isinstance(part, dict) and "text" in part:
                parts.append(str(part["text"]))
        return "\n".join(parts)
    return str(content)


def _append_response_format_instruction(
    instructions: str,
    response_format: Mapping[str, Any],
) -> str:
    fmt_type = response_format.get("type")
    suffix = ""
    if fmt_type == "json_object":
        suffix = "Return only a valid JSON object. Do not wrap it in Markdown."
    elif fmt_type == "json_schema":
        suffix = "Return only JSON matching the requested schema."
    if not suffix:
        return instructions
    return f"{instructions}\n\n{suffix}" if instructions else suffix


def _usage_from_completed(event: dict[str, Any]) -> Usage | None:
    response = event.get("response") or {}
    usage = response.get("usage") or event.get("usage")
    if not isinstance(usage, dict):
        return None
    return Usage(
        prompt_tokens=usage.get("input_tokens") or usage.get("prompt_tokens"),
        completion_tokens=usage.get("output_tokens")
        or usage.get("completion_tokens"),
    )


def _normalise_finish_reason(value: str | None):
    if value in ("stop", "length", "content_filter"):
        return value
    return None
