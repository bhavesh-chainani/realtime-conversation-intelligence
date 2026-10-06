"""The one LLM client (LiteLLM / OpenAI-compatible, async) and model selection helpers."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from openai import AsyncOpenAI

from . import config as cfg
from .assemblyai import loaded_ssl_context

logger = logging.getLogger(__name__)

T = TypeVar("T")

_async_client: AsyncOpenAI | None = None
_async_client_loop: int | None = None


def llm_is_configured() -> bool:
    return bool(cfg.LLM_API_KEY and cfg.LLM_BASE_URL and cfg.SUGGESTION_MODEL)


def get_suggestion_model() -> str:
    return cfg.SUGGESTION_MODEL


def get_extraction_model() -> str:
    return cfg.EXTRACTION_MODEL


def get_wrapup_model() -> str:
    return cfg.WRAPUP_MODEL


def strip_code_fences(raw: str) -> str:
    """Model JSON replies sometimes arrive wrapped in a ```json fence."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def get_async_llm_client() -> AsyncOpenAI | None:
    """The shared client, with long-lived keep-alive connections (one per event loop, since httpx
    pools are loop-bound). Reuses the TLS context loaded at startup: building one per client means
    reading the CA bundle, which can take seconds."""
    global _async_client, _async_client_loop
    if not cfg.LLM_API_KEY or not cfg.LLM_BASE_URL:
        return None
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0
    if _async_client is None or _async_client_loop != loop_id:
        _async_client = AsyncOpenAI(
            api_key=cfg.LLM_API_KEY,
            base_url=cfg.LLM_BASE_URL,
            timeout=cfg.LLM_TIMEOUT_SECONDS,
            max_retries=cfg.LLM_MAX_RETRIES,
            http_client=httpx.AsyncClient(
                timeout=cfg.LLM_TIMEOUT_SECONDS,
                verify=loaded_ssl_context() or True,
                limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=120),
            ),
        )
        _async_client_loop = loop_id
    return _async_client


def supports_reasoning_effort(model: str) -> bool:
    """GPT-5 and o-series models take `reasoning_effort`; others (GPT-4.x, Gemini, Claude) reject it.
    Gateway names carry provider prefixes, e.g. "openai.global.gpt-5.4-mini"."""
    name = model.lower()
    if "chat" in name:
        return False
    return bool(re.search(r"(^|\.)(gpt-5|o\d)", name))


def llm_extra_params(model: str, live: bool = False) -> dict[str, Any]:
    """Optional provider params for calls to `model`; `live` calls (Staff are waiting on them) also get
    the configured service tier."""
    params: dict[str, Any] = {}
    if cfg.LLM_REASONING_EFFORT and supports_reasoning_effort(model):
        params["reasoning_effort"] = cfg.LLM_REASONING_EFFORT
    if live and cfg.LLM_SERVICE_TIER:
        params["service_tier"] = cfg.LLM_SERVICE_TIER
    return params


async def hedged(make_call: Callable[[], Awaitable[T]], after_s: float) -> tuple[T, bool]:
    """Run `make_call()`; if it has not answered after `after_s` (or fails before then), start an
    identical second call and return whichever succeeds first, cancelling the other. Returns
    (result, whether a second call was made).

    The gateway has random multi-second stalls, and some models occasionally return malformed output;
    a second request usually beats both.
    """
    first = asyncio.ensure_future(make_call())
    if after_s <= 0:
        return await first, False
    calls = [first]
    try:
        done, _ = await asyncio.wait(calls, timeout=after_s)
        if done and first.exception() is None:
            return first.result(), False
        calls.append(asyncio.ensure_future(make_call()))
        pending: set[asyncio.Future] = set(calls)
        error: BaseException | None = None
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for call in done:
                if call.exception() is None:
                    return call.result(), True
                error = call.exception()
        raise error  # both calls failed
    finally:
        for call in calls:
            if not call.done():
                call.cancel()


_last_warm_up = 0.0
KEEP_WARM_EVERY_S = 25


async def warm_up(min_interval_s: float = 60) -> None:
    """Open two gateway connections (a tiny completion and a model list, in parallel), so a call's
    first suggestion and entity extraction both find a warm one. Throttled; failures are only logged."""
    global _last_warm_up
    client = get_async_llm_client()
    if not client or time.monotonic() - _last_warm_up < min_interval_s:
        return
    _last_warm_up = time.monotonic()
    started = time.perf_counter()
    model = get_suggestion_model()
    try:
        await asyncio.gather(
            client.chat.completions.create(
                model=model,
                max_completion_tokens=16,
                messages=[{"role": "user", "content": "Reply with OK."}],
                **llm_extra_params(model, live=True),
            ),
            client.models.list(),
        )
        logger.info("[llm] warm-up in %.0fms", (time.perf_counter() - started) * 1000)
    except Exception as exc:
        logger.warning("[llm] warm-up failed: %s: %s", type(exc).__name__, exc)


async def keep_warm() -> None:
    """While a call is live, touch the gateway every KEEP_WARM_EVERY_S (a model list costs no tokens)
    so quiet stretches do not let the connection close. Runs until cancelled."""
    client = get_async_llm_client()
    if not client:
        return
    while True:
        await asyncio.sleep(KEEP_WARM_EVERY_S)
        try:
            await client.models.list()
        except Exception as exc:
            logger.debug("[llm] keep-warm failed: %s", exc)
