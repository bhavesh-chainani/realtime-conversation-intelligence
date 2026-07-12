"""Worker dispatch: run one inference job from ledger row."""

from __future__ import annotations

import logging
from typing import Any

from .customer_data_extractor import extractor
from .persistence import persist_customer_extract_event, persist_suggestion_event
from .suggestions_core import compute_suggestions

logger = logging.getLogger(__name__)


async def dispatch_inference_job(record: dict[str, Any]) -> dict[str, Any]:
    job_type = record.get("job_type")
    payload = record.get("payload") or {}
    user_key = record.get("user_key") or ""

    session_id = payload.get("session_id")

    if job_type == "suggestions":
        ctx = payload.get("context") or ""
        mx = int(payload.get("max_suggestions") or 2)
        body = await compute_suggestions(ctx, mx)
        err = body.get("error") if isinstance(body.get("error"), str) else None
        persist_suggestion_event(session_id, user_key, ctx, body, error=err)
        return body

    if job_type == "customer_extract":
        transcript = payload.get("conversation_transcript") or ""
        extracted = await extractor.extract(transcript)
        body: dict[str, Any] = {"success": True, "data": extracted}
        persist_customer_extract_event(
            session_id, user_key, transcript, True, extracted
        )
        return body

    raise ValueError(f"unknown job_type {job_type!r}")
