"""Suggestion agent (the principal agent): reads the live transcript plus any customer record and
suggests what Staff should say next, in one LLM round trip.

The prompt also gets the caller card (what Staff already know, and where the history check stands) and
the suggestion Staff currently see, so it steers towards identifying the caller first and does not repeat
itself. The customer record is rendered in so suggestions can cite prior cases by ID; IDs that are not
in the record are dropped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from .. import clock, issue_guides
from .. import config as cfg
from ..customer_history import format_caller_card, format_customer_record, verified_case_ids
from ..json_stream import partial_string_value
from ..llm import (
    get_async_llm_client,
    get_suggestion_llm_client,
    get_suggestion_model,
    hedged,
    llm_extra_params,
    strip_code_fences,
)
from ..prompt_loader import fallback_suggestions, system_prompt, user_prompt
from ..text_guard import ForeignScriptError, contains_foreign_script

logger = logging.getLogger(__name__)

# Gets the growing "possibleConversation" text while the answer streams, so Staff can start reading it.
OnDraft = Callable[[str], None]
PARSE_ATTEMPTS = 2  # a malformed answer is asked again once before falling back


def validate_suggestion(suggestion: Any) -> dict[str, Any] | None:
    """Normalise one model-produced suggestion into the shape the UI expects."""
    if not isinstance(suggestion, dict):
        return None
    # The prompt asks for possibleConversation first and flat, so it streams first; older answers nest it.
    nested = suggestion.get("details") if isinstance(suggestion.get("details"), dict) else {}
    details = {
        **nested,
        **{k: suggestion[k] for k in ("possibleConversation", "priority") if k in suggestion},
    }
    try:
        confidence = float(suggestion.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.7
    linked = suggestion.get("linked_records")
    return {
        "type": suggestion.get("type") or "Next Steps",
        "topic": suggestion.get("topic") or "Follow up with the caller.",
        "confidence": confidence,
        "linked_records": [str(x).strip() for x in linked if str(x).strip()]
        if isinstance(linked, list)
        else [],
        "details": {
            "possibleConversation": details.get("possibleConversation")
            or "Could you tell me a bit more about your situation?",
            "priority": details.get("priority") or "medium",
        },
    }


def suggestion_system_prompt() -> str:
    """The system prompt plus the service guide: both fixed for the life of the process, so the
    provider's prompt cache covers the whole ~3,600-token prefix."""
    return f"{system_prompt('suggestion')}\n\n{issue_guides.render()}"


async def generate_suggestions(
    conversation_transcript: str,
    max_suggestions: int = cfg.SUGGESTION_MAX,
    customer_profile: dict[str, Any] | None = None,
    customer_cases: list[dict[str, Any]] | None = None,
    previous_suggestions: list[str] | None = None,
    on_draft: OnDraft | None = None,
) -> dict[str, Any]:
    """Return {suggestions, timings}, streaming the first suggestion's wording to `on_draft` as it is
    written. Raises on LLM/parse failure."""
    max_suggestions = max(1, min(5, max_suggestions or cfg.SUGGESTION_MAX))
    known_case_ids = verified_case_ids(customer_profile, customer_cases)

    client = get_suggestion_llm_client()
    if not client:
        raise ValueError("LLM client is not configured")

    model = get_suggestion_model()
    started = time.perf_counter()
    messages = [
        {"role": "system", "content": suggestion_system_prompt()},
        {
            "role": "user",
            "content": user_prompt(
                "suggestion",
                today=clock.today_label(),
                calendar=clock.calendar_label(),
                customer_record=format_customer_record(customer_profile, customer_cases, clock.today()),
                caller_card=format_caller_card(customer_profile),
                previous_suggestions="\n".join(f"- {s}" for s in previous_suggestions or []) or "none",
                conversation_transcript=conversation_transcript,
                max_suggestions=max_suggestions,
            ),
        },
    ]
    # Output length is most of the wait here, so ask only for the suggestions that are shown, and stream them
    # so the wording shows as it is written. No client retries: a stalled call is covered by the hedge, and
    # the whole answer has one deadline (after which suggest_with_fallback shows the fallback).
    # (client, model, uses the suggestion host's settings): the first request, then the hedge's second one.
    backup = get_async_llm_client() if cfg.SUGGESTION_HEDGE_MODEL else None
    routes = [(client, model, True)]
    routes.append((backup, cfg.SUGGESTION_HEDGE_MODEL, False) if backup else routes[0])
    calls: list[int] = []  # requests started for the current answer, to pick each one's route

    async def open_stream() -> tuple[Any, Any, str, str]:
        """Start one streamed request and wait for its first text, so the hedge races on time to first token."""
        route_client, route_model, own_host = routes[min(len(calls), 1)]
        calls.append(1)
        stream = await route_client.with_options(max_retries=0).chat.completions.create(
            model=route_model,
            temperature=cfg.SUGGESTION_TEMPERATURE,
            max_completion_tokens=cfg.SUGGESTION_MAX_TOKENS,
            timeout=cfg.SUGGESTION_TIMEOUT_SECONDS,
            response_format={"type": "json_object"},
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},
            **llm_extra_params(route_model, live=True, suggestion=own_host),
        )
        chunks = stream.__aiter__()
        try:
            while True:
                if text := _chunk_text(await anext(chunks)):
                    return stream, chunks, text, route_model
        except BaseException:
            await stream.close()
            raise

    async def close_stream(opened: tuple[Any, Any, str, str]) -> None:
        await opened[0].close()

    async def answer() -> tuple[dict[str, Any], int | None, bool, str]:
        """One streamed answer, parsed. A malformed one is asked again once."""
        hedged_any, attempts = False, 0
        while True:
            calls.clear()
            (stream, chunks, text, answered_by), was_hedged = await hedged(
                open_stream, cfg.SUGGESTION_HEDGE_FIRST_TOKEN_MS / 1000, discard=close_stream
            )
            hedged_any = hedged_any or was_hedged
            attempts += 1
            parts, cached, shown = [text], None, ""
            try:
                while True:
                    draft = partial_string_value("".join(parts), "possibleConversation")
                    if on_draft and draft and draft != shown:
                        shown = draft
                        on_draft(clock.fix_weekdays(draft))
                    chunk = await anext(chunks, None)
                    if chunk is None:
                        break
                    cached = _cached_tokens(chunk) or cached
                    parts.append(_chunk_text(chunk))
            finally:
                await stream.close()
            try:
                parsed = json.loads(strip_code_fences("".join(parts)))
                if not isinstance(parsed, dict):
                    raise ValueError("Model did not return a JSON object")
                return parsed, cached, hedged_any, answered_by
            except ValueError as exc:
                if attempts >= PARSE_ATTEMPTS:
                    raise
                logger.warning("[suggestion] malformed answer, asking again: %s", exc)

    parsed, cached_tokens, was_hedged, model = await asyncio.wait_for(
        answer(), cfg.SUGGESTION_TIMEOUT_SECONDS
    )
    llm_ms = (time.perf_counter() - started) * 1000

    suggestions: list[dict[str, Any]] = []
    dropped_foreign = 0
    raw_items = parsed.get("suggestions")
    for item in (raw_items if isinstance(raw_items, list) else [])[:max_suggestions]:
        validated = validate_suggestion(item)
        if validated is None:
            continue
        validated["topic"] = clock.fix_weekdays(validated["topic"])
        details = validated["details"]
        details["possibleConversation"] = clock.fix_weekdays(details["possibleConversation"])
        if contains_foreign_script(validated):
            dropped_foreign += 1
            continue
        # Only keep case IDs that really exist in the record we sent.
        validated["linked_records"] = [c for c in validated["linked_records"] if c in known_case_ids]
        suggestions.append(validated)

    if dropped_foreign and not suggestions:
        # Surface as a failure so the caller returns the fallback instead.
        raise ForeignScriptError("Model output contained non-English text")

    return {
        "suggestions": suggestions,
        "timings": {
            "llm_ms": round(llm_ms, 1),
            "model": model,
            "hedged": was_hedged,
            "cached_tokens": cached_tokens,
        },
    }


def _chunk_text(chunk: Any) -> str:
    choices = getattr(chunk, "choices", None) or []
    delta = getattr(choices[0], "delta", None) if choices else None
    return getattr(delta, "content", None) or ""


def _cached_tokens(chunk: Any) -> int | None:
    """Cached prompt tokens, from the usage chunk at the end of the stream."""
    details = getattr(getattr(chunk, "usage", None), "prompt_tokens_details", None)
    return getattr(details, "cached_tokens", None)


async def suggest_with_fallback(
    conversation_transcript: str,
    max_suggestions: int,
    customer_profile: dict[str, Any] | None = None,
    customer_cases: list[dict[str, Any]] | None = None,
    previous_suggestions: list[str] | None = None,
    on_draft: OnDraft | None = None,
) -> dict[str, Any]:
    """Suggestions from the agent, or the static fallback (flagged `fallback`) if it fails."""
    started = time.perf_counter()
    try:
        body = await generate_suggestions(
            conversation_transcript,
            max_suggestions=max_suggestions,
            customer_profile=customer_profile,
            customer_cases=customer_cases,
            previous_suggestions=previous_suggestions,
            on_draft=on_draft,
        )
    except Exception as exc:
        logger.warning("Suggestion agent failed, using fallback: %s: %s", type(exc).__name__, exc)
        body = {"suggestions": fallback_suggestions()[:max_suggestions], "fallback": True, "timings": {}}
    timings = body["timings"]
    timings["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    logger.info(
        "[suggestion] %s suggestions in %sms (llm %sms, cached %s tokens, cases=%s%s)",
        len(body["suggestions"]),
        timings["total_ms"],
        timings.get("llm_ms", "-"),
        timings.get("cached_tokens", "-"),
        len(customer_cases or []),
        ", hedged" if timings.get("hedged") else "",
    )
    return body
