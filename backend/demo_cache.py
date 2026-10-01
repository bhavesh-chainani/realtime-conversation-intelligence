"""Demo scenarios + warm suggestion cache.

The cache pre-computes the suggestion response for every Customer line of a
scripted scenario, using the same pipeline and the same customer context the
live frontend would have at that point in the call. The frontend shows a cached
card only when the live call is slow, and replaces it when the live one lands.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any

from . import config as cfg
from .call_summary import compute_call_summary
from .customer_history import customer_history_service
from .llm import get_suggestion_model
from .prompt_loader import PROMPTS_DIR
from .quick_entities import extract_intro_name, extract_nric
from .suggestions_core import compute_suggestions

logger = logging.getLogger(__name__)

WRAPUP_STEP = "_wrapup"
_SCENARIO_ID = re.compile(r"^[a-z0-9_\-]{1,80}$")
_memory_cache: dict[str, dict[str, Any]] = {}
_build_locks: dict[str, asyncio.Lock] = {}


class ScenarioNotFound(Exception):
    pass


def _scenario_path(scenario_id: str):
    if not _SCENARIO_ID.match(scenario_id or ""):
        raise ScenarioNotFound(scenario_id)
    path = cfg.DEMO_SCRIPTS_DIR / f"{scenario_id}.json"
    if not path.is_file():
        raise ScenarioNotFound(scenario_id)
    return path


def load_scenario(scenario_id: str) -> dict[str, Any]:
    return json.loads(_scenario_path(scenario_id).read_text(encoding="utf-8"))


def list_scenarios() -> list[dict[str, Any]]:
    out = []
    for path in sorted(cfg.DEMO_SCRIPTS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("[demo] skipping unreadable scenario %s", path.name)
            continue
        out.append(
            {
                "id": data.get("id", path.stem),
                "title": data.get("title", path.stem),
                "description": data.get("description", ""),
            }
        )
    return out


def scenario_stt_config(scenario_id: str) -> tuple[list[str], str]:
    """Scenario keyterms merged with the global ones (deduped, max 100) + STT prompt."""
    scenario = load_scenario(scenario_id)
    merged: list[str] = []
    seen: set[str] = set()
    for term in [*cfg.ASSEMBLYAI_KEYTERMS, *scenario.get("keyterms", [])]:
        clean = str(term).strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            merged.append(clean)
    return merged[:100], str(scenario.get("stt_prompt") or "").strip()


def role_label(role: str) -> str:
    return "Staff" if role == "staff" else "Customer"


def transcript_upto(lines: list[dict[str, Any]], index: int) -> str:
    return "\n".join(
        f"{role_label(line['role'])}: {line['text']}" for line in lines[: index + 1]
    )


def known_context_by_line(
    lines: list[dict[str, Any]],
) -> dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Customer profile + cases known after each line, mirroring the frontend's quick path."""
    profile: dict[str, Any] = {}
    cases: list[dict[str, Any]] = []
    looked_up_by_id = False
    lookups: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    out: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}

    for line in lines:
        if line["role"] == "customer":
            nric = extract_nric(line["text"]) or extract_nric(line.get("say", ""))
            name = extract_intro_name(line["text"])
            key: tuple[str | None, str | None] | None = None
            if nric and not looked_up_by_id:
                profile["nric_worker_permit_id"] = nric
                key = (None, nric)
                looked_up_by_id = True
            elif name and not profile.get("name") and not looked_up_by_id:
                profile["name"] = name
                key = (name, None)
            if key is not None:
                if key not in lookups:
                    lookups[key] = customer_history_service.lookup(*key)
                result = lookups[key]
                if result.get("status") == "ok":
                    profile["record_match"] = result.get("match_strategy")
                    customer = result.get("customer") or {}
                    for field in ("name", "nric_worker_permit_id", "address"):
                        if customer.get(field):
                            profile[field] = customer[field]
                    cases = list(result.get("cases") or [])
        out[line["id"]] = (dict(profile), list(cases))
    return out


def prompt_hash(scenario_id: str) -> str:
    """Changes whenever anything that affects cached output changes."""
    digest = hashlib.sha1()
    digest.update(_scenario_path(scenario_id).read_bytes())
    for name in (
        "suggestion_fast_system_prompt.txt",
        "suggestion_fast_user_prompt.txt",
        "call_summary_system_prompt.txt",
    ):
        digest.update((PROMPTS_DIR / name).read_bytes())
    digest.update(
        f"{get_suggestion_model()}|{cfg.LLM_REASONING_EFFORT}|{cfg.SUGGESTION_PIPELINE}|{cfg.SUGGESTION_MAX}".encode()
    )
    return digest.hexdigest()[:16]


def _cache_file(scenario_id: str):
    return cfg.DEMO_CACHE_DIR / f"{scenario_id}.json"


def get_cache(scenario_id: str) -> dict[str, Any] | None:
    _scenario_path(scenario_id)
    if scenario_id not in _memory_cache:
        path = _cache_file(scenario_id)
        if path.is_file():
            try:
                _memory_cache[scenario_id] = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("[demo] ignoring unreadable cache file %s", path)
                return None
    return _memory_cache.get(scenario_id)


def cache_status(scenario_id: str) -> dict[str, Any]:
    scenario = load_scenario(scenario_id)
    # One step per customer line, plus the end-of-call wrap-up.
    total = sum(1 for line in scenario["lines"] if line["role"] == "customer") + 1
    cache = get_cache(scenario_id)
    if not cache:
        return {"scenario_id": scenario_id, "built": False, "fresh": False, "steps": 0, "total": total}
    return {
        "scenario_id": scenario_id,
        "built": True,
        "fresh": cache.get("prompt_hash") == prompt_hash(scenario_id),
        "steps": len(cache.get("steps", {})),
        "total": total,
        "built_at": cache.get("built_at"),
        "model": cache.get("model"),
        "build_ms": cache.get("build_ms"),
    }


async def build_cache(scenario_id: str, concurrency: int = 3) -> dict[str, Any]:
    lock = _build_locks.setdefault(scenario_id, asyncio.Lock())
    async with lock:
        scenario = load_scenario(scenario_id)
        lines = scenario["lines"]
        context_by_line = await asyncio.to_thread(known_context_by_line, lines)
        semaphore = asyncio.Semaphore(concurrency)
        started = time.perf_counter()

        async def build_step(index: int) -> tuple[str, dict[str, Any] | None]:
            line = lines[index]
            profile, cases = context_by_line[line["id"]]
            async with semaphore:
                body = await compute_suggestions(
                    transcript_upto(lines, index),
                    max_suggestions=cfg.SUGGESTION_MAX,
                    customer_profile=profile,
                    customer_cases=cases,
                    pipeline="single",
                )
            if body.get("fallback"):
                logger.warning("[demo] cache step %s failed: %s", line["id"], body.get("error"))
                return line["id"], None
            return line["id"], {**body, "customer_cases": [c.get("case_id") for c in cases]}

        async def build_wrapup() -> tuple[str, dict[str, Any] | None]:
            profile, cases = context_by_line[lines[-1]["id"]]
            async with semaphore:
                body = await compute_call_summary(
                    transcript_upto(lines, len(lines) - 1), profile, cases
                )
            if body.get("fallback"):
                logger.warning("[demo] wrap-up cache step failed: %s", body.get("error"))
                return WRAPUP_STEP, None
            return WRAPUP_STEP, body

        results = await asyncio.gather(
            *(build_step(i) for i, line in enumerate(lines) if line["role"] == "customer"),
            build_wrapup(),
        )
        cache = {
            "scenario_id": scenario_id,
            "prompt_hash": prompt_hash(scenario_id),
            "model": get_suggestion_model(),
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "build_ms": round((time.perf_counter() - started) * 1000),
            "steps": {line_id: body for line_id, body in results if body is not None},
        }
        _memory_cache[scenario_id] = cache
        try:
            cfg.DEMO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _cache_file(scenario_id).write_text(json.dumps(cache, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("[demo] could not persist cache: %s", exc)
        return cache_status(scenario_id)
