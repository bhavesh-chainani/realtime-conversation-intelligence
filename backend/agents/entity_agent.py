"""Entity agent: extracts the caller's name, contact number, email and purpose of call from the transcript.

The orchestrator runs it alongside the instant regex (backend/quick_entities.py) and uses what it
finds to look the caller up in the customer DB.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .. import config as cfg
from ..llm import (
    get_async_llm_client,
    get_extraction_model,
    llm_extra_params,
    strip_code_fences,
)
from ..profile import FIELDS
from ..prompt_loader import system_prompt, user_prompt
from ..quick_entities import (
    extract_email_from_transcript,
    extract_phone_from_transcript,
    normalize_email,
    normalize_phone,
)
from ..text_guard import has_foreign_script

logger = logging.getLogger(__name__)

PLACEHOLDER_VALUES = {
    "",
    "n/a",
    "na",
    "none",
    "null",
    "nil",
    "unknown",
    "not mentioned",
    "not provided",
    "not available",
    "not given",
    "not stated",
    "unspecified",
}
EXTRACTION_MAX_TOKENS = 300  # four short fields; purpose_of_call is the longest
GENERIC_NAMES = {"customer", "caller", "unknown customer"}


async def extract_entities(transcript: str) -> dict[str, str | None]:
    """Caller fields found in `transcript` (None where not given). Raises if the LLM call fails."""
    if len((transcript or "").strip()) < 10:
        return dict.fromkeys(FIELDS)

    client = get_async_llm_client()
    if not client:
        raise ValueError("LLM client is not configured")

    # Not on the critical path, but it shares the gateway with the suggestion: keep it short, and no
    # client retries (the next customer turn extracts again anyway).
    response = await client.with_options(max_retries=0).chat.completions.create(
        model=get_extraction_model(),
        temperature=0,
        max_completion_tokens=EXTRACTION_MAX_TOKENS,
        timeout=cfg.EXTRACTION_TIMEOUT_SECONDS,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt("entity")},
            {
                "role": "user",
                "content": user_prompt("entity", conversation_transcript=transcript),
            },
        ],
        **llm_extra_params(get_extraction_model(), live=True),
    )
    data = normalize_payload(json.loads(strip_code_fences(response.choices[0].message.content or "")))
    data["contact_number"] = reconcile_phone(data["contact_number"], transcript)
    data["email"] = reconcile_email(data["email"], transcript)
    # Drop any field the model wrote partly in another script; staff see it blank instead.
    for name, value in data.items():
        if value and has_foreign_script(value):
            data[name] = None
    logger.info("[entity] captured %s", [f for f in FIELDS if data.get(f)])
    return data


def normalize_payload(payload: Any) -> dict[str, str | None]:
    raw = payload if isinstance(payload, dict) else {}
    return {name: normalize_value(name, raw.get(name)) for name in FIELDS}


def normalize_value(name: str, value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).strip())
    if text.lower() in PLACEHOLDER_VALUES:
        return None
    # A malformed phone / email is kept as said so staff can see and fix it.
    if name == "contact_number":
        return normalize_phone(text) or text
    if name == "email":
        return normalize_email(text) or text
    if name == "name" and text.lower() in GENERIC_NAMES:
        return None
    return text


def reconcile_phone(llm_value: str | None, transcript: str) -> str | None:
    """Prefer a well-formed phone number from the model; otherwise a regex hit on Customer lines."""
    if normalize_phone(llm_value):
        return llm_value
    return extract_phone_from_transcript(transcript) or llm_value


def reconcile_email(llm_value: str | None, transcript: str) -> str | None:
    """Prefer a well-formed email from the model; otherwise a regex hit on Customer lines."""
    if normalize_email(llm_value):
        return llm_value
    return extract_email_from_transcript(transcript) or llm_value
