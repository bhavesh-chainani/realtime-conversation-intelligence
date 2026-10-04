"""End-of-call wrap-up: drafts case notes from the transcript and the customer record."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from . import config as cfg
from .llm import get_async_llm_client, get_suggestion_model, llm_extra_params, str_list, strip_code_fences
from .prompt_loader import format_customer_record, load_prompt, verified_case_ids
from .suggestions import CustomerCase
from .text_guard import ForeignScriptError, contains_foreign_script

logger = logging.getLogger(__name__)

router = APIRouter()

SUMMARY_MAX_TOKENS = 700
_system_prompt: str | None = None


def get_call_summary_system_prompt() -> str:
    global _system_prompt
    if _system_prompt is None:
        _system_prompt = load_prompt("call_summary_system_prompt.txt")
    return _system_prompt


async def generate_call_summary(
    conversation_transcript: str,
    customer_profile: Optional[Dict[str, Any]] = None,
    customer_cases: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return the drafted notes. Raises on LLM/parse failure."""
    known_case_ids = verified_case_ids(customer_profile, customer_cases)

    client = get_async_llm_client()
    if not client:
        raise ValueError("LLM client is not configured")

    model = get_suggestion_model()
    started = time.perf_counter()
    response = await client.chat.completions.create(
        model=model,
        temperature=0.2,
        max_completion_tokens=SUMMARY_MAX_TOKENS,
        timeout=cfg.SUGGESTION_TIMEOUT_SECONDS * 2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": get_call_summary_system_prompt()},
            {
                "role": "user",
                "content": (
                    f"{format_customer_record(customer_profile, customer_cases)}\n\n"
                    f"CALL TRANSCRIPT:\n{conversation_transcript}\n\n"
                    "Write the after-call notes. Return the JSON object only."
                ),
            },
        ],
        **llm_extra_params(),
    )
    llm_ms = round((time.perf_counter() - started) * 1000, 1)

    parsed = json.loads(strip_code_fences(response.choices[0].message.content or ""))
    if not isinstance(parsed, dict):
        raise ValueError("Model did not return a JSON object")
    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        raise ValueError("Model returned an empty summary")

    notes = {
        "summary": summary,
        "issue": str(parsed.get("issue") or "").strip(),
        "linked_records": [
            cid for cid in str_list(parsed.get("linked_records"), 10) if cid in known_case_ids
        ],
        "actions": str_list(parsed.get("actions"), 4),
        "documents_requested": str_list(parsed.get("documents_requested"), 6),
        "follow_up": str(parsed.get("follow_up") or "").strip(),
    }
    if contains_foreign_script(notes):
        raise ForeignScriptError("Model output contained non-English text")
    return {**notes, "timings": {"llm_ms": llm_ms, "model": model}}


async def compute_call_summary(
    conversation_transcript: str,
    customer_profile: Optional[Dict[str, Any]] = None,
    customer_cases: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        try:
            body = await generate_call_summary(
                conversation_transcript, customer_profile, customer_cases
            )
        except ForeignScriptError:
            # A stray non-English word is a sampling glitch; one retry almost always fixes it.
            logger.warning("[call-summary] non-English output, retrying once")
            body = await generate_call_summary(
                conversation_transcript, customer_profile, customer_cases
            )
        body["timings"]["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return body
    except Exception as exc:
        logger.error("[call-summary] failed: %s: %s", type(exc).__name__, exc)
        return {
            "summary": "",
            "fallback": True,
            "error": str(exc) or type(exc).__name__,
            "timings": {"total_ms": round((time.perf_counter() - started) * 1000, 1)},
        }


class CallSummaryRequest(BaseModel):
    context: str
    customer_profile: Dict[str, Any] | None = None
    customer_history: List[CustomerCase] | None = None


@router.post("/call-summary")
async def call_summary(req: CallSummaryRequest) -> Dict[str, Any]:
    return await compute_call_summary(
        req.context,
        customer_profile=req.customer_profile,
        customer_cases=[c.model_dump() for c in req.customer_history or []],
    )
