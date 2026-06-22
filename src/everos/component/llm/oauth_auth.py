"""Helpers for local OAuth/bearer auth files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_bearer_auth(auth_file: Path) -> tuple[str, str | None]:
    """Return ``(access_token, account_id)`` from a local auth JSON file.

    Supported shapes:
      - Codex ``~/.codex/auth.json``: ``{"tokens": {"access_token": ...}}``
      - Grok ``~/.grok/auth.json``: ``{"issuer::client": {"key": ...}}``
      - Flat token files: ``{"access_token": ...}``, ``{"token": ...}``

    The caller is responsible for keeping the file refreshed. This helper
    intentionally does not log or return redacted token material.
    """
    data = json.loads(auth_file.expanduser().read_text())
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else data
    access_token = _first_str(tokens, "access_token", "token", "bearer_token", "key")
    account_id = _first_str(tokens, "account_id", "chatgpt_account_id")
    if not access_token:
        nested = _single_nested_dict(tokens)
        if nested is not None:
            access_token = _first_str(
                nested, "access_token", "token", "bearer_token", "key"
            )
            account_id = _first_str(nested, "account_id", "chatgpt_account_id")
    if not access_token:
        raise ValueError(f"missing access token in {auth_file}")
    return access_token, account_id


def _first_str(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _single_nested_dict(data: dict[str, Any]) -> dict[str, Any] | None:
    dict_values = [value for value in data.values() if isinstance(value, dict)]
    if len(dict_values) == 1:
        return dict_values[0]
    return None
