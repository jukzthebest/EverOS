"""``build_llm_provider`` — settings validation + provider build."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from everos.component.llm import build_llm_provider
from everos.component.llm.codex_oauth_provider import CodexOAuthProvider
from everos.component.llm.fallback_provider import FallbackLLMProvider
from everos.component.llm.grok_oauth_provider import GrokOAuthProvider
from everos.component.llm.openai_provider import OpenAIProvider
from everos.config.settings import LLMSettings


def test_raises_when_api_key_missing() -> None:
    s = LLMSettings(model="m", api_key=None, base_url="https://x")
    with pytest.raises(ValueError, match="EVEROS_LLM__API_KEY"):
        build_llm_provider(s)


def test_raises_when_api_key_empty() -> None:
    s = LLMSettings(model="m", api_key=SecretStr(""), base_url="https://x")
    with pytest.raises(ValueError, match="EVEROS_LLM__API_KEY"):
        build_llm_provider(s)


def test_raises_when_base_url_missing() -> None:
    s = LLMSettings(model="m", api_key=SecretStr("k"), base_url=None)
    with pytest.raises(ValueError, match="EVEROS_LLM__BASE_URL"):
        build_llm_provider(s)


def test_builds_openai_provider() -> None:
    s = LLMSettings(model="m", api_key=SecretStr("k"), base_url="https://x")
    p = build_llm_provider(s)
    assert isinstance(p, OpenAIProvider)


def test_builds_codex_oauth_provider_without_api_key() -> None:
    s = LLMSettings(provider="codex_oauth", model="gpt-5.5")
    p = build_llm_provider(s)
    assert isinstance(p, CodexOAuthProvider)


def test_builds_grok_oauth_provider_with_default_cli_auth() -> None:
    s = LLMSettings(provider="grok_oauth", model="grok-build")
    p = build_llm_provider(s)
    assert isinstance(p, GrokOAuthProvider)


def test_builds_grok_oauth_provider(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"access_token": "tok"}', encoding="utf-8")
    s = LLMSettings(
        provider="grok_oauth",
        model="grok-4",
        auth_file=auth_file,
        base_url="https://api.x.ai/v1",
    )
    p = build_llm_provider(s)
    assert isinstance(p, GrokOAuthProvider)


def test_builds_fallback_chain_skipping_unconfigured_grok() -> None:
    s = LLMSettings(
        provider_chain=["grok_oauth", "codex_oauth", "openai"],
        model="gpt-5.5",
        grok_model="grok-build",
        codex_model="gpt-5.5",
    )

    p = build_llm_provider(s)

    assert isinstance(p, FallbackLLMProvider)
    assert p.provider_names == ["grok_oauth", "codex_oauth"]


def test_builds_fallback_chain_with_openai_tail(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"access_token": "tok"}', encoding="utf-8")
    s = LLMSettings(
        provider_chain=["grok_oauth", "codex_oauth", "openai"],
        model="gpt-5.5",
        grok_model="grok-build",
        codex_model="gpt-5.5",
        openai_model="gpt-4o-mini",
        auth_file=auth_file,
        base_url="https://api.x.ai/v1",
        api_key=SecretStr("sk-test"),
    )

    p = build_llm_provider(s)

    assert isinstance(p, FallbackLLMProvider)
    assert p.provider_names == ["grok_oauth", "codex_oauth", "openai"]


def test_provider_specific_models_are_used_for_fallback() -> None:
    s = LLMSettings(
        provider_chain=["grok_oauth", "codex_oauth"],
        model="fallback-model",
        grok_model="grok-build",
        codex_model="gpt-5.5",
    )

    p = build_llm_provider(s)

    assert isinstance(p, FallbackLLMProvider)
    grok = p._providers[0][1]
    codex = p._providers[1][1]
    assert isinstance(grok, GrokOAuthProvider)
    assert isinstance(codex, CodexOAuthProvider)
    assert grok._model == "grok-build"
    assert codex._model == "gpt-5.5"


def test_fallback_chain_does_not_share_openai_base_url_with_oauth() -> None:
    s = LLMSettings(
        provider_chain=["grok_oauth", "codex_oauth", "openai"],
        base_url="https://api.openai.com/v1",
        api_key=SecretStr("sk-test"),
        grok_model="grok-build",
        codex_model="gpt-5.5",
    )

    p = build_llm_provider(s)

    assert isinstance(p, FallbackLLMProvider)
    grok = p._providers[0][1]
    codex = p._providers[1][1]
    assert isinstance(grok, GrokOAuthProvider)
    assert isinstance(codex, CodexOAuthProvider)
    assert grok._base_url == "https://cli-chat-proxy.grok.com/v1"
    assert codex._url == "wss://chatgpt.com/backend-api/codex/responses"


def test_empty_fallback_chain_configuration_fails() -> None:
    s = LLMSettings(
        provider_chain=["openai"],
        model="m",
    )

    with pytest.raises(ValueError, match="no configured providers"):
        build_llm_provider(s)
