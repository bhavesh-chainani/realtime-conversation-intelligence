"""Entity agent: extracts the caller's name, NRIC / FIN, address and purpose of call from the transcript.

The orchestrator runs it alongside the instant regex (backend/quick_entities.py) and uses what it
finds to look the caller up in the customer DB.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .. import config as cfg
from ..llm import get_async_llm_client, get_extraction_model, llm_extra_params, strip_code_fences
from ..profile import FIELDS
from ..prompt_loader import system_prompt, user_prompt
from ..quick_entities import NRIC_PATTERN, extract_nric_from_transcript
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
GENERIC_NAMES = {"customer", "caller", "unknown customer"}


async def extract_entities(transcript: str) -> dict[str, str | None]:
    """Caller fields found in `transcript` (None where not given). Raises if the LLM call fails."""
    if len((transcript or "").strip()) < 10:
        return dict.fromkeys(FIELDS)

    client = get_async_llm_client()
    if not client:
        raise ValueError("LLM client is not configured")

    response = await client.chat.completions.create(
        model=get_extraction_model(),
        temperature=cfg.SUGGESTION_TEMPERATURE,
        timeout=cfg.EXTRACTION_TIMEOUT_SECONDS,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt("entity")},
            {"role": "user", "content": user_prompt("entity", conversation_transcript=transcript)},
        ],
        **llm_extra_params(),
    )
    data = normalize_payload(json.loads(strip_code_fences(response.choices[0].message.content or "")))
    data["nric_worker_permit_id"] = reconcile_id(data["nric_worker_permit_id"], transcript)
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
    text = str(value).strip()
    text = re.sub(r"\s+", "", text).upper() if name == "nric_worker_permit_id" else re.sub(r"\s+", " ", text)
    if text.lower() in PLACEHOLDER_VALUES:
        return None
    if name == "name" and text.lower() in GENERIC_NAMES:
        return None
    return text


def reconcile_id(llm_value: str | None, transcript: str) -> str | None:
    """Prefer a well-formed ID from the model; otherwise a regex hit on Customer lines."""
    if llm_value and NRIC_PATTERN.fullmatch(llm_value.lower()):
        return llm_value
    return extract_nric_from_transcript(transcript) or llm_value
