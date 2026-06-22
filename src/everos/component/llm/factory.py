"""Factory for building an LLM provider from :class:`LLMSettings`."""

from __future__ import annotations

from everos.config import LLMSettings

from .codex_oauth_provider import CodexOAuthProvider
from .fallback_provider import FallbackLLMProvider
from .grok_oauth_provider import GrokOAuthProvider
from .openai_provider import OpenAIProvider
from .protocol import LLMClient


def build_llm_provider(settings: LLMSettings) -> LLMClient:
    """Build an LLM provider from settings.

    Unwraps :class:`pydantic.SecretStr` here so downstream callers never
    touch the raw key directly. Provider-specific required fields fail
    fast here so misconfiguration is surfaced at startup.

    Args:
        settings: The :class:`LLMSettings` slice from
            :func:`everos.config.load_settings`.

    Returns:
        A provider that structurally satisfies
        :class:`everalgo.llm.LLMClient` and can be passed to everalgo
        operators via ``llm=``.

    Raises:
        ValueError: If the selected provider is missing required config.
    """
    if settings.provider_chain:
        return _build_fallback_provider(settings)
    return _build_single_provider(settings, settings.provider)


def _build_single_provider(
    settings: LLMSettings,
    provider: str,
    *,
    skip_missing: bool = False,
) -> LLMClient | None:
    if provider == "codex_oauth":
        return CodexOAuthProvider(
            model=_model_for(settings, provider),
            auth_file=_auth_file_for(settings, provider),
            base_url=_base_url_for(settings, provider),
            service_tier=settings.service_tier or "priority",
            originator=settings.originator,
            user_agent=settings.user_agent,
        )

    if provider == "grok_oauth":
        return GrokOAuthProvider(
            model=_model_for(settings, provider),
            auth_file=_auth_file_for(settings, provider),
            base_url=_base_url_for(settings, provider),
        )

    api_key = settings.api_key.get_secret_value() if settings.api_key else ""
    if not api_key:
        if skip_missing:
            return None
        raise ValueError(
            "LLM api_key is not configured "
            "(set EVEROS_LLM__API_KEY or [llm] api_key in user toml)"
        )
    base_url = _base_url_for(settings, provider)
    if not base_url:
        if skip_missing:
            return None
        raise ValueError(
            "LLM base_url is not configured "
            "(set EVEROS_LLM__BASE_URL or [llm] base_url in user toml)"
        )
    return OpenAIProvider(
        model=_model_for(settings, provider),
        api_key=api_key,
        base_url=base_url,
    )


def _model_for(settings: LLMSettings, provider: str) -> str:
    if provider == "grok_oauth":
        return settings.grok_model or settings.model
    if provider == "codex_oauth":
        return settings.codex_model or settings.model
    if provider == "openai":
        return settings.openai_model or settings.model
    return settings.model


def _base_url_for(settings: LLMSettings, provider: str) -> str | None:
    use_shared = not settings.provider_chain
    if provider == "grok_oauth":
        return settings.grok_base_url or (settings.base_url if use_shared else None)
    if provider == "codex_oauth":
        return settings.codex_base_url or (settings.base_url if use_shared else None)
    if provider == "openai":
        return settings.openai_base_url or settings.base_url
    return settings.base_url


def _auth_file_for(settings: LLMSettings, provider: str):
    use_shared = not settings.provider_chain
    if provider == "grok_oauth":
        return settings.grok_auth_file or (settings.auth_file if use_shared else None)
    if provider == "codex_oauth":
        return settings.codex_auth_file or (settings.auth_file if use_shared else None)
    return settings.auth_file


def _build_fallback_provider(settings: LLMSettings) -> FallbackLLMProvider:
    providers: list[tuple[str, LLMClient]] = []
    skipped: list[str] = []
    for provider_name in settings.provider_chain:
        provider = _build_single_provider(
            settings,
            provider_name,
            skip_missing=True,
        )
        if provider is None:
            skipped.append(provider_name)
            continue
        providers.append((provider_name, provider))
    if not providers:
        skipped_msg = ", ".join(skipped) if skipped else "<none>"
        raise ValueError(
            "LLM provider_chain has no configured providers "
            f"(skipped: {skipped_msg})"
        )
    return FallbackLLMProvider(providers)
