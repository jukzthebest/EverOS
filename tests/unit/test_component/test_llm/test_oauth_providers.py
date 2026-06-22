"""OAuth-backed LLM provider helpers."""

from __future__ import annotations

import json

from everos.component.llm.codex_oauth_provider import CodexOAuthProvider
from everos.component.llm.oauth_auth import load_bearer_auth
from everos.component.llm.protocol import ChatMessage


def test_load_bearer_auth_supports_codex_shape(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "access",
                    "account_id": "account",
                }
            }
        ),
        encoding="utf-8",
    )

    assert load_bearer_auth(auth_file) == ("access", "account")


def test_load_bearer_auth_supports_flat_shape(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"token": "access"}', encoding="utf-8")

    assert load_bearer_auth(auth_file) == ("access", None)


def test_load_bearer_auth_supports_grok_cli_shape(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        '{"https://auth.x.ai::client": {"key": "access", "refresh_token": "r"}}',
        encoding="utf-8",
    )

    assert load_bearer_auth(auth_file) == ("access", None)


def test_codex_request_splits_system_into_instructions(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"tokens": {"access_token": "access"}}', encoding="utf-8")
    provider = CodexOAuthProvider(model="gpt-5.5", auth_file=auth_file)

    payload = provider._build_request(
        model="gpt-5.5",
        messages=[
            ChatMessage(role="system", content="Extract memory."),
            ChatMessage(role="user", content="hello"),
        ],
        response_format={"type": "json_object"},
    )

    assert payload["type"] == "response.create"
    assert payload["model"] == "gpt-5.5"
    assert "Extract memory." in payload["instructions"]
    assert "valid JSON object" in payload["instructions"]
    assert payload["input"] == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hello"}],
        }
    ]


def test_codex_headers_never_expose_token_in_key_names(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        '{"tokens": {"access_token": "access", "account_id": "account"}}',
        encoding="utf-8",
    )
    provider = CodexOAuthProvider(model="gpt-5.5", auth_file=auth_file)

    headers = provider._headers()

    assert headers["Authorization"] == "Bearer access"
    assert headers["chatgpt-account-id"] == "account"
    assert "access" not in ",".join(headers.keys())
