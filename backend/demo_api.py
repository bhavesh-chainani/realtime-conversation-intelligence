"""Demo-only endpoints (mounted when DEMO_MODE=true): scenarios, warm cache, prewarm, preflight."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from . import config as cfg
from .assemblyai import StreamingTokenError, create_streaming_token
from .auth import require_api_auth
from .customer_history import customer_history_service
from .demo_cache import (
    ScenarioNotFound,
    build_cache,
    cache_status,
    get_cache,
    list_scenarios,
    load_scenario,
)
from .diarization import nemotron
from .llm import (
    get_async_llm_client,
    get_extraction_model,
    get_llm_client,
    get_suggestion_model,
    llm_extra_params,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demo", tags=["demo"])

_PING_MESSAGES = [{"role": "user", "content": "Reply with the single word: ok"}]


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


async def _ping_async(model: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        client = get_async_llm_client()
        if not client:
            raise ValueError("LLM client is not configured")
        await client.chat.completions.create(
            model=model, messages=_PING_MESSAGES, max_completion_tokens=16, **llm_extra_params()
        )
        return {"ok": True, "model": model, "ms": _ms(started)}
    except Exception as exc:
        return {"ok": False, "model": model, "ms": _ms(started), "error": str(exc)[:200]}


async def _ping_sync(model: str) -> dict[str, Any]:
    """Warms the sync client's pool, which customer extraction uses."""
    started = time.perf_counter()
    try:
        client = get_llm_client()
        if not client:
            raise ValueError("LLM client is not configured")
        await asyncio.to_thread(
            client.chat.completions.create,
            model=model,
            messages=_PING_MESSAGES,
            max_completion_tokens=16,
            **llm_extra_params(),
        )
        return {"ok": True, "model": model, "ms": _ms(started)}
    except Exception as exc:
        return {"ok": False, "model": model, "ms": _ms(started), "error": str(exc)[:200]}


async def _check_db() -> dict[str, Any]:
    persona: dict[str, Any] = {}
    try:
        scenarios = list_scenarios()
        if scenarios:
            persona = load_scenario(scenarios[0]["id"]).get("persona", {})
    except Exception:
        pass
    probe_id = persona.get("nric") or "S0000000X"
    started = time.perf_counter()
    result = await asyncio.to_thread(customer_history_service.lookup, None, probe_id)
    ok = result.get("status") in {"ok", "not_found"}
    return {
        "ok": ok and (not persona or result.get("status") == "ok"),
        "ms": _ms(started),
        "status": result.get("status"),
        "probe": probe_id,
        "cases": len(result.get("cases") or []),
        "address": bool((result.get("customer") or {}).get("address")),
    }


async def prewarm() -> dict[str, Any]:
    """Open LLM keep-alive connections (both clients) and the DB path before the first turn."""
    started = time.perf_counter()
    suggestion, extraction, db = await asyncio.gather(
        _ping_async(get_suggestion_model()),
        _ping_sync(get_extraction_model()),
        _check_db(),
    )
    out = {"suggestion": suggestion, "extraction": extraction, "db": db, "ms": _ms(started)}
    logger.info(
        "[demo] prewarm done in %sms (suggestion %sms, extraction %sms, db %sms)",
        out["ms"],
        suggestion["ms"],
        extraction["ms"],
        db["ms"],
    )
    return out


def _scenario_or_404(scenario_id: str) -> dict[str, Any]:
    try:
        return load_scenario(scenario_id)
    except ScenarioNotFound:
        raise HTTPException(status_code=404, detail="Unknown scenario")


@router.get("/scenarios")
async def scenarios(_: str = Depends(require_api_auth)) -> list[dict[str, Any]]:
    return list_scenarios()


@router.get("/scenarios/{scenario_id}")
async def scenario(scenario_id: str, _: str = Depends(require_api_auth)) -> dict[str, Any]:
    return _scenario_or_404(scenario_id)


@router.get("/cache/{scenario_id}")
async def get_scenario_cache(
    scenario_id: str, _: str = Depends(require_api_auth)
) -> dict[str, Any]:
    _scenario_or_404(scenario_id)
    cache = get_cache(scenario_id) or {}
    return {**cache_status(scenario_id), "steps": cache.get("steps", {})}


@router.post("/cache/{scenario_id}/build")
async def build_scenario_cache(
    scenario_id: str, _: str = Depends(require_api_auth)
) -> dict[str, Any]:
    _scenario_or_404(scenario_id)
    return await build_cache(scenario_id)


@router.post("/prewarm")
async def prewarm_endpoint(_: str = Depends(require_api_auth)) -> dict[str, Any]:
    return await prewarm()


@router.get("/preflight")
async def preflight(
    scenario_id: str | None = None, _: str = Depends(require_api_auth)
) -> dict[str, Any]:
    async def stt() -> dict[str, Any]:
        started = time.perf_counter()
        if cfg.DIARIZATION_BACKEND == "nemotron" and nemotron.get_diarizer() is None:
            error = nemotron.load_error() or "loading"
            return {"ok": False, "ms": 0, "error": f"Nemotron diarizer not ready: {error}"}
        try:
            await create_streaming_token(expires_in_seconds=60)
            return {"ok": True, "ms": _ms(started)}
        except StreamingTokenError as exc:
            return {"ok": False, "ms": _ms(started), "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "ms": _ms(started), "error": str(exc)[:200]}

    llm, db, stt_result = await asyncio.gather(
        _ping_async(get_suggestion_model()), _check_db(), stt()
    )
    llm["ok"] = llm["ok"] and llm["ms"] < 2500

    cache: dict[str, Any] = {"ok": False, "detail": "no scenario selected"}
    if scenario_id:
        _scenario_or_404(scenario_id)
        status = cache_status(scenario_id)
        cache = {
            **status,
            "ok": status["fresh"] and status["steps"] == status["total"],
        }

    limits = {
        "ok": cfg.RATE_LIMIT_PER_MINUTE >= 300,
        "rate_limit_per_minute": cfg.RATE_LIMIT_PER_MINUTE,
        "daily_request_quota": cfg.DAILY_REQUEST_QUOTA,
    }
    checks = {"llm": llm, "db": db, "stt": stt_result, "cache": cache, "limits": limits}
    return {"ok": all(c["ok"] for c in checks.values()), **checks}
