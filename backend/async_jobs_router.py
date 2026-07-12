"""REST: enqueue inference jobs."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from pydantic import BaseModel, Field

from .auth import enforce_usage_limits
from .config import ASYNC_JOBS_ENABLED, INFERENCE_QUEUE_MODE
from .inference_queue import publish_inference_job
from .job_store import JOB_LEDGER, NullJobLedger

router = APIRouter(tags=["async-jobs"])


class QueueSuggestPayload(BaseModel):
    context: str
    max_suggestions: int = 2
    session_id: str | None = None


class QueueExtractPayload(BaseModel):
    conversation_transcript: str
    session_id: str | None = None


def _ensure_async_jobs() -> None:
    if not ASYNC_JOBS_ENABLED:
        raise HTTPException(status_code=503, detail="Async jobs disabled")
    if isinstance(JOB_LEDGER, NullJobLedger):
        raise HTTPException(status_code=503, detail="Job store not configured")


@router.post("/queue/suggestions")
async def enqueue_suggestions(
    body: QueueSuggestPayload,
    user_key: str = Depends(enforce_usage_limits),
) -> dict[str, Any]:
    _ensure_async_jobs()
    job_id = str(uuid.uuid4())
    payload = {
        "context": body.context,
        "max_suggestions": body.max_suggestions,
        "session_id": body.session_id,
    }
    JOB_LEDGER.create_pending(
        job_id=job_id,
        user_key=user_key,
        job_type="suggestions",
        payload=payload,
    )
    publish_inference_job(job_id)
    return {"job_id": job_id, "status": "pending"}


@router.post("/queue/extract-customer-data")
async def enqueue_extract_customer(
    body: QueueExtractPayload,
    user_key: str = Depends(enforce_usage_limits),
) -> dict[str, Any]:
    _ensure_async_jobs()
    job_id = str(uuid.uuid4())
    payload = {
        "conversation_transcript": body.conversation_transcript,
        "session_id": body.session_id,
    }
    JOB_LEDGER.create_pending(
        job_id=job_id,
        user_key=user_key,
        job_type="customer_extract",
        payload=payload,
    )
    publish_inference_job(job_id)
    return {"job_id": job_id, "status": "pending"}


@router.get("/queue/jobs/{job_id}")
async def get_inference_job(job_id: str, user_key: str = Depends(enforce_usage_limits)):
    _ensure_async_jobs()
    row = JOB_LEDGER.get_owned(job_id, user_key)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    resp: dict[str, Any] = {
        "job_id": row["job_id"],
        "job_type": row.get("job_type"),
        "status": row["status"],
        "error": row.get("error"),
    }
    if row.get("result") is not None:
        resp["result"] = row["result"]

    hint = ""
    if INFERENCE_QUEUE_MODE == "poll":
        hint = (
            "Queue mode is poll: ensure inference worker container is running."
        )

    resp["queue_mode"] = INFERENCE_QUEUE_MODE
    resp["hint"] = hint.strip()
    return resp
