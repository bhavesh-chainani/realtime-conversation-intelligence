"""End-of-call wrap-up: drafts case notes from the transcript and the customer record."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from . import config as cfg
from .auth import enforce_usage_limits
from .llm import get_async_llm_client, get_suggestion_model, llm_extra_params
from .prompt_loader import format_customer_record, load_prompt
from .suggestions import CustomerCase

logger = logging.getLogger(__name__)

router = APIRouter()

SUMMARY_MAX_TOKENS = 700
_system_prompt: str | None = None


def get_call_summary_system_prompt() -> str:
    global _system_prompt
    if _system_prompt is None:
        _system_prompt = load_prompt("call_summary_system_prompt.txt")
    return _system_prompt


def _str_list(value: Any, limit: int) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:limit]


def _strip_code_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


async def generate_call_summary(
    conversation_transcript: str,
    customer_profile: Optional[Dict[str, Any]] = None,
    customer_cases: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return the drafted notes. Raises on LLM/parse failure."""
    known_case_ids = {
        str(c.get("case_id")).strip()
        for c in (customer_cases or [])
        if isinstance(c, dict) and c.get("case_id")
    }
    # A name-only match is unverified: never reference cases in the notes.
    if (customer_profile or {}).get("record_match") == "name":
        known_case_ids = set()

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

    parsed = json.loads(_strip_code_fences(response.choices[0].message.content or ""))
    if not isinstance(parsed, dict):
        raise ValueError("Model did not return a JSON object")
    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        raise ValueError("Model returned an empty summary")

    return {
        "summary": summary,
        "issue": str(parsed.get("issue") or "").strip(),
        "linked_records": [
            cid for cid in _str_list(parsed.get("linked_records"), 10) if cid in known_case_ids
        ],
        "actions": _str_list(parsed.get("actions"), 4),
        "documents_requested": _str_list(parsed.get("documents_requested"), 6),
        "follow_up": str(parsed.get("follow_up") or "").strip(),
        "timings": {"llm_ms": llm_ms, "model": model},
    }


async def compute_call_summary(
    conversation_transcript: str,
    customer_profile: Optional[Dict[str, Any]] = None,
    customer_cases: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
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
    session_id: str | None = Field(None, description="Reserved for persistence")


@router.post("/call-summary")
async def call_summary(
    req: CallSummaryRequest, _: str = Depends(enforce_usage_limits)
) -> Dict[str, Any]:
    return await compute_call_summary(
        req.context,
        customer_profile=req.customer_profile,
        customer_cases=[c.model_dump() for c in req.customer_history or []],
    )
