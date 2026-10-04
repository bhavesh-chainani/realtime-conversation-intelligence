"""Suggestion agent (the principal agent): reads the live transcript plus any customer record and
suggests what Staff should say next, in one LLM round trip.

The customer record (from the entity agent's DB lookup) is rendered into the prompt so suggestions
can cite prior cases by ID; IDs that are not in the record are dropped.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .. import config as cfg
from ..customer_history import format_customer_record, verified_case_ids
from ..llm import get_async_llm_client, get_suggestion_model, llm_extra_params, str_list, strip_code_fences
from ..prompt_loader import fallback_suggestions, system_prompt, user_prompt
from ..text_guard import ForeignScriptError, contains_foreign_script

logger = logging.getLogger(__name__)


def validate_suggestion(suggestion: Any) -> dict[str, Any] | None:
    """Normalise one model-produced suggestion into the shape the UI expects."""
    if not isinstance(suggestion, dict):
        return None

    try:
        confidence = float(suggestion.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.7

    validated: dict[str, Any] = {
        "type": suggestion.get("type", "General Suggestion"),
        "topic": suggestion.get(
            "topic",
            suggestion.get("text", "Follow up with the caller to gather more information."),
        ),
        "confidence": confidence,
        "details": suggestion.get("details", {}),
    }
    if not isinstance(validated["details"], dict):
        validated["details"] = {}
    details = validated["details"]

    details.setdefault("possibleConversation", "Could you provide more details about your situation?")
    details.setdefault("priority", suggestion.get("priority", "medium"))

    linked = suggestion.get("linked_records")
    if isinstance(linked, list):
        validated["linked_records"] = [str(x).strip() for x in linked if str(x).strip()]
    if isinstance(suggestion.get("source"), str):
        validated["source"] = suggestion["source"].strip().lower()

    return validated


async def generate_suggestions(
    conversation_transcript: str,
    max_suggestions: int = 2,
    customer_profile: dict[str, Any] | None = None,
    customer_cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return {suggestions, decision, timings}. Raises on LLM/parse failure."""
    max_suggestions = max(1, min(5, int(max_suggestions or 2)))
    customer_record = format_customer_record(customer_profile, customer_cases)
    known_case_ids = verified_case_ids(customer_profile, customer_cases)

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
            {"role": "system", "content": system_prompt("suggestion")},
            {
                "role": "user",
                "content": user_prompt(
                    "suggestion",
                    conversation_transcript=conversation_transcript,
                    max_suggestions=max_suggestions,
                    customer_record=customer_record,
                ),
            },
        ],
        **llm_extra_params(),
    )
    llm_ms = (time.perf_counter() - started) * 1000

    parsed = json.loads(strip_code_fences(response.choices[0].message.content or ""))
    if not isinstance(parsed, dict):
        raise ValueError("Model did not return a JSON object")

    suggestions: list[dict[str, Any]] = []
    dropped_foreign = 0
    raw_items = parsed.get("suggestions")
    for item in (raw_items if isinstance(raw_items, list) else [])[:max_suggestions]:
        validated = validate_suggestion(item)
        if validated is None:
            continue
        if contains_foreign_script(validated):
            dropped_foreign += 1
            continue
        # Only keep case IDs that really exist in the record we sent.
        linked = [cid for cid in validated.get("linked_records", []) if cid in known_case_ids]
        validated["linked_records"] = linked
        validated["source"] = "history" if linked else "conversation"
        suggestions.append(validated)

    if dropped_foreign and not suggestions:
        # Surface as a failure so the caller returns the fallback instead.
        raise ForeignScriptError("Model output contained non-English text")

    should_suggest = bool(parsed.get("should_suggest", True)) or bool(suggestions)
    return {
        "suggestions": suggestions if should_suggest else [],
        "decision": {
            "should_suggest": should_suggest,
            "known_info": str_list(parsed.get("known_info")),
            "missing_info": str_list(parsed.get("missing_info")),
        },
        "timings": {"llm_ms": round(llm_ms, 1), "model": model},
    }


async def suggest_with_fallback(
    conversation_transcript: str,
    max_suggestions: int,
    customer_profile: dict[str, Any] | None = None,
    customer_cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Suggestions from the agent, or the static fallback (flagged `fallback`) if it fails."""
    started = time.perf_counter()
    try:
        body = await generate_suggestions(
            conversation_transcript,
            max_suggestions=max_suggestions,
            customer_profile=customer_profile,
            customer_cases=customer_cases,
        )
    except Exception as exc:
        logger.warning("Suggestion agent failed, using fallback: %s: %s", type(exc).__name__, exc)
        body = {
            "suggestions": fallback_suggestions()[:max_suggestions],
            "error": str(exc) or type(exc).__name__,
            "fallback": True,
            "timings": {},
        }
    body["timings"]["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    logger.info(
        "[suggestion] %s suggestions in %sms (cases=%s)",
        len(body["suggestions"]),
        body["timings"]["total_ms"],
        len(customer_cases or []),
    )
    return body
