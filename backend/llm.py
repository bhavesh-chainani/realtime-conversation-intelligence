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

# (event loop id, base URL, API key) -> client
_clients: dict[tuple[int, str, str], AsyncOpenAI] = {}


def llm_is_configured() -> bool:
    return bool(
        cfg.LLM_API_KEY
        and cfg.LLM_BASE_URL
        and cfg.SUGGESTION_API_KEY
        and cfg.SUGGESTION_BASE_URL
        and cfg.SUGGESTION_MODEL
    )


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


def _client(api_key: str, base_url: str) -> AsyncOpenAI | None:
    """A shared client per host, with long-lived keep-alive connections (one per event loop, since httpx
    pools are loop-bound). Reuses the TLS context loaded at startup: building one per client means
    reading the CA bundle, which can take seconds."""
    if not api_key or not base_url:
        return None
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0
    key = (loop_id, base_url, api_key)
    if key not in _clients:
        # Clients bound to an earlier event loop cannot be reused.
        for old in [k for k in _clients if k[0] != loop_id]:
            del _clients[old]
        _clients[key] = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=cfg.LLM_TIMEOUT_SECONDS,
            max_retries=cfg.LLM_MAX_RETRIES,
            http_client=httpx.AsyncClient(
                timeout=cfg.LLM_TIMEOUT_SECONDS,
                verify=loaded_ssl_context() or True,
                limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=120),
            ),
        )
    return _clients[key]


def get_async_llm_client() -> AsyncOpenAI | None:
    """The client for the LLM_* host (entity extraction, wrap-up, the simulator)."""
    return _client(cfg.LLM_API_KEY, cfg.LLM_BASE_URL)


def get_suggestion_llm_client() -> AsyncOpenAI | None:
    """The client for the suggestion agent: its own host when SUGGESTION_BASE_URL is set, else the same one."""
    return _client(cfg.SUGGESTION_API_KEY, cfg.SUGGESTION_BASE_URL)


def supports_reasoning_effort(model: str) -> bool:
    """GPT-5, gpt-oss and o-series models take `reasoning_effort`; others (GPT-4.x, Gemini, Claude) reject
    it. Gateway names carry provider prefixes, e.g. "openai.global.gpt-5.4-mini" or "openai/gpt-oss-120b"."""
    name = model.lower()
    if "chat" in name:
        return False
    return bool(re.search(r"(^|[./])(gpt-5|gpt-oss|o\d)", name))


def llm_extra_params(model: str, live: bool = False, suggestion: bool = False) -> dict[str, Any]:
    """Optional provider params for calls to `model`; `live` calls (Staff are waiting on them) also get
    the configured service tier. `suggestion` calls use the suggestion host's settings."""
    effort = cfg.SUGGESTION_REASONING_EFFORT if suggestion else cfg.LLM_REASONING_EFFORT
    tier = cfg.SUGGESTION_SERVICE_TIER if suggestion else cfg.LLM_SERVICE_TIER
    params: dict[str, Any] = {}
    if effort and supports_reasoning_effort(model):
        params["reasoning_effort"] = effort
    if live and tier:
        params["service_tier"] = tier
    return params


async def hedged(
    make_call: Callable[[], Awaitable[T]],
    after_s: float,
    discard: Callable[[T], Awaitable[None]] | None = None,
) -> tuple[T, bool]:
    """Run `make_call()`; if it has not answered after `after_s` (or fails before then), start an
    identical second call and return whichever succeeds first, cancelling the other. Returns
    (result, whether a second call was made). If both succeed at once, `discard` gets the unused result
    (e.g. to close a stream).

    The gateway has random multi-second stalls; a second request usually beats them.
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
            winners = [call for call in done if call.exception() is None]
            if winners:
                for extra in winners[1:]:
                    if discard:
                        await discard(extra.result())
                return winners[0].result(), True
            error = next(iter(done)).exception()
        raise error  # both calls failed
    finally:
        for call in calls:
            if not call.done():
                call.cancel()


_last_warm_up = 0.0
KEEP_WARM_EVERY_S = 25


async def warm_up(min_interval_s: float = 60) -> None:
    """Open two connections in parallel: a tiny completion on the suggestion host and a model list on the
    LLM_* host (the same host unless SUGGESTION_BASE_URL is set), so a call's first suggestion and entity
    extraction both find a warm one. Throttled; failures are only logged."""
    global _last_warm_up
    suggestion_client, client = get_suggestion_llm_client(), get_async_llm_client()
    if not suggestion_client or not client or time.monotonic() - _last_warm_up < min_interval_s:
        return
    _last_warm_up = time.monotonic()
    started = time.perf_counter()
    model = get_suggestion_model()
    try:
        await asyncio.gather(
            suggestion_client.chat.completions.create(
                model=model,
                max_completion_tokens=16,
                messages=[{"role": "user", "content": "Reply with OK."}],
                **llm_extra_params(model, live=True, suggestion=True),
            ),
            client.models.list(),
        )
        logger.info("[llm] warm-up in %.0fms", (time.perf_counter() - started) * 1000)
    except Exception as exc:
        logger.warning("[llm] warm-up failed: %s: %s", type(exc).__name__, exc)


async def keep_warm() -> None:
    """While a call is live, touch each LLM host every KEEP_WARM_EVERY_S (a model list costs no tokens)
    so quiet stretches do not let the connections close. Runs until cancelled."""
    clients = {id(c): c for c in (get_async_llm_client(), get_suggestion_llm_client()) if c}
    if not clients:
        return
    while True:
        await asyncio.sleep(KEEP_WARM_EVERY_S)
        for client in clients.values():
            try:
                await client.models.list()
            except Exception as exc:
                logger.debug("[llm] keep-warm failed: %s", exc)
