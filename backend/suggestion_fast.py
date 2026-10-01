"""Single-call suggestion pipeline: router decision + suggestions in one LLM round trip.

Used when SUGGESTION_PIPELINE=single (the demo default). Customer DB context is
rendered into the prompt so suggestions can cite prior cases by ID.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from . import config as cfg
from .llm import get_async_llm_client, get_suggestion_model, llm_extra_params
from .prompt_loader import (
    format_customer_record,
    get_suggestion_fast_system_prompt,
    get_suggestion_fast_user_prompt,
)
from .suggestion_agent import validate_suggestion

logger = logging.getLogger(__name__)


def _strip_code_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _as_str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


async def generate_fast(
    conversation_transcript: str,
    max_suggestions: int = 2,
    customer_profile: Optional[Dict[str, Any]] = None,
    customer_cases: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return {suggestions, router_decision, timings}. Raises on LLM/parse failure."""
    max_suggestions = max(1, min(5, int(max_suggestions or 2)))
    customer_record = format_customer_record(customer_profile, customer_cases)
    known_case_ids = {
        str(c.get("case_id")).strip()
        for c in (customer_cases or [])
        if isinstance(c, dict) and c.get("case_id")
    }
    # A name-only match is unverified: never surface case references yet.
    if (customer_profile or {}).get("record_match") == "name":
        known_case_ids = set()

    client = get_async_llm_client()
    if not client:
        raise ValueError("LLM client is not configured")

    model = get_suggestion_model()
    started = time.perf_counter()
    response = await client.chat.completions.create(
        model=model,
        temperature=cfg.SUGGESTION_TEMPERATURE,
        max_completion_tokens=cfg.SUGGESTION_MAX_TOKENS,
        timeout=cfg.SUGGESTION_TIMEOUT_SECONDS,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": get_suggestion_fast_system_prompt()},
            {
                "role": "user",
                "content": get_suggestion_fast_user_prompt(
                    conversation_transcript, max_suggestions, customer_record
                ),
            },
        ],
        **llm_extra_params(),
    )
    llm_ms = (time.perf_counter() - started) * 1000

    parsed = json.loads(_strip_code_fences(response.choices[0].message.content or ""))
    if not isinstance(parsed, dict):
        raise ValueError("Model did not return a JSON object")

    suggestions: List[Dict[str, Any]] = []
    raw_items = parsed.get("suggestions")
    for item in (raw_items if isinstance(raw_items, list) else [])[:max_suggestions]:
        validated = validate_suggestion(item)
        if validated is None:
            continue
        # Only keep case IDs that really exist in the record we sent.
        linked = [cid for cid in validated.get("linked_records", []) if cid in known_case_ids]
        validated["linked_records"] = linked
        validated["source"] = "history" if linked else "conversation"
        suggestions.append(validated)

    should_suggest = bool(parsed.get("should_suggest", True)) or bool(suggestions)
    return {
        "suggestions": suggestions if should_suggest else [],
        "router_decision": {
            "should_suggest": should_suggest,
            "confidence": 1.0 if should_suggest else 0.0,
            "reason": "single-call pipeline",
            "known_info": _as_str_list(parsed.get("known_info")),
            "missing_info": _as_str_list(parsed.get("missing_info")),
        },
        "timings": {"llm_ms": round(llm_ms, 1), "model": model},
    }
