"""Shared LiteLLM/OpenAI-compatible client + model selection helpers."""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from . import config as cfg

_client: OpenAI | None = None
_client_signature: tuple[str, str, float] | None = None


def _clean(value: str | None) -> str:
    return (value or "").strip()


def resolve_llm_api_key() -> str:
    """Return the configured LiteLLM/OpenAI-compatible proxy credential."""
    return _clean(cfg.LLM_API_KEY)


def resolve_llm_base_url() -> str:
    """Return the configured LiteLLM/OpenAI-compatible proxy base URL."""
    return _clean(cfg.LLM_BASE_URL)


def get_router_model() -> str:
    return _clean(cfg.ROUTER_MODEL)


def get_suggestion_model() -> str:
    return _clean(cfg.SUGGESTION_MODEL) or get_router_model()


def get_extraction_model() -> str:
    return _clean(cfg.EXTRACTION_MODEL) or get_suggestion_model()


def get_sql_lookup_model() -> str:
    return _clean(cfg.SQL_LOOKUP_MODEL) or get_extraction_model()


def llm_runtime_config() -> dict[str, Any]:
    router_model = get_router_model()
    suggestion_model = get_suggestion_model()
    extraction_model = get_extraction_model()
    sql_lookup_model = get_sql_lookup_model()
    api_key_loaded = bool(resolve_llm_api_key())
    base_url_configured = bool(resolve_llm_base_url())

    return {
        "llm_api_key_loaded": api_key_loaded,
        "llm_base_url_configured": base_url_configured,
        "router_model": router_model,
        "router_model_configured": bool(router_model),
        "suggestion_model": suggestion_model,
        "suggestion_model_configured": bool(suggestion_model),
        "extraction_model": extraction_model,
        "extraction_model_configured": bool(extraction_model),
        "sql_lookup_model": sql_lookup_model,
        "sql_lookup_model_configured": bool(sql_lookup_model),
        "llm_configured": (
            api_key_loaded
            and base_url_configured
            and bool(router_model)
            and bool(suggestion_model)
            and bool(extraction_model)
        ),
    }


def llm_is_configured() -> bool:
    return bool(llm_runtime_config()["llm_configured"])


def get_llm_client() -> OpenAI | None:
    """Create/cache one OpenAI-compatible client pointed at LiteLLM."""
    global _client, _client_signature

    api_key = resolve_llm_api_key()
    base_url = resolve_llm_base_url()
    if not api_key or not base_url:
        return None

    timeout = float(cfg.LLM_TIMEOUT_SECONDS)
    signature = (api_key, base_url, timeout)

    if _client is None or _client_signature != signature:
        _client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        _client_signature = signature

    return _client


def reset_llm_client_cache() -> None:
    """Clear the cached client. Useful in tests after monkeypatching config."""
    global _client, _client_signature
    _client = None
    _client_signature = None
