"""The one LLM client (LiteLLM / OpenAI-compatible, async) and model selection helpers."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from openai import AsyncOpenAI

from . import config as cfg

_async_client: AsyncOpenAI | None = None
_async_client_signature: tuple[str, str, float, int, int] | None = None


def _clean(value: str | None) -> str:
    return (value or "").strip()


def resolve_llm_api_key() -> str:
    """Return the configured LiteLLM/OpenAI-compatible proxy credential."""
    return _clean(cfg.LLM_API_KEY)


def resolve_llm_base_url() -> str:
    """Return the configured LiteLLM/OpenAI-compatible proxy base URL."""
    return _clean(cfg.LLM_BASE_URL)


def get_suggestion_model() -> str:
    return _clean(cfg.SUGGESTION_MODEL)


def get_extraction_model() -> str:
    return _clean(cfg.EXTRACTION_MODEL) or get_suggestion_model()


def llm_runtime_config() -> dict[str, Any]:
    suggestion_model = get_suggestion_model()
    extraction_model = get_extraction_model()
    api_key_loaded = bool(resolve_llm_api_key())
    base_url_configured = bool(resolve_llm_base_url())

    return {
        "llm_api_key_loaded": api_key_loaded,
        "llm_base_url_configured": base_url_configured,
        "suggestion_model": suggestion_model,
        "suggestion_model_configured": bool(suggestion_model),
        "extraction_model": extraction_model,
        "extraction_model_configured": bool(extraction_model),
        "llm_configured": (
            api_key_loaded and base_url_configured and bool(suggestion_model) and bool(extraction_model)
        ),
    }


def strip_code_fences(raw: str) -> str:
    """Model JSON replies sometimes arrive wrapped in a ```json fence."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def str_list(value: Any, limit: int | None = None) -> list[str]:
    """Non-empty strings from a model-returned list (anything else -> [])."""
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:limit]


def llm_is_configured() -> bool:
    return bool(llm_runtime_config()["llm_configured"])


def get_async_llm_client() -> AsyncOpenAI | None:
    """Create/cache a non-blocking client with long-lived keep-alive connections.

    Keyed on the running event loop too, since httpx async pools are loop-bound.
    """
    global _async_client, _async_client_signature

    api_key = resolve_llm_api_key()
    base_url = resolve_llm_base_url()
    if not api_key or not base_url:
        return None

    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0
    timeout = float(cfg.LLM_TIMEOUT_SECONDS)
    max_retries = int(cfg.LLM_MAX_RETRIES)
    signature = (api_key, base_url, timeout, max_retries, loop_id)

    if _async_client is None or _async_client_signature != signature:
        _async_client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            http_client=httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=120),
            ),
        )
        _async_client_signature = signature

    return _async_client


def llm_extra_params() -> dict[str, Any]:
    """Optional provider params shared by latency-sensitive calls."""
    params: dict[str, Any] = {}
    if cfg.LLM_REASONING_EFFORT:
        params["reasoning_effort"] = cfg.LLM_REASONING_EFFORT
    return params


def reset_llm_client_cache() -> None:
    """Clear the cached clients. Useful in tests after monkeypatching config."""
    global _async_client, _async_client_signature
    _async_client = None
    _async_client_signature = None
