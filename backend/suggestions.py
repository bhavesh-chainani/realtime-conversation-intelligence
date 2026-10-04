"""POST /suggest: the principal agent behind an HTTP endpoint, with a static fallback."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import config as cfg
from .prompt_loader import get_fallback_suggestions
from .suggestion_agent import generate_suggestions

logger = logging.getLogger(__name__)

router = APIRouter()


class CustomerCase(BaseModel):
    case_id: str
    company: str | None = None
    type: str | None = None
    status: str | None = None
    summary: str | None = None


class SuggestRequest(BaseModel):
    context: str
    max_suggestions: int | None = Field(
        None, description="Defaults to MAX_SUGGESTIONS from config"
    )
    customer_profile: Dict[str, Any] | None = Field(
        None, description="Verified/extracted customer fields to ground suggestions"
    )
    customer_history: List[CustomerCase] | None = Field(
        None, description="Prior cases from the customer history lookup"
    )


async def compute_suggestions(
    context: str,
    max_suggestions: int = 2,
    customer_profile: Optional[Dict[str, Any]] = None,
    customer_cases: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Suggestions from the principal agent, or the static fallback if it fails."""
    started = time.perf_counter()
    try:
        body = await generate_suggestions(
            context,
            max_suggestions=max_suggestions,
            customer_profile=customer_profile,
            customer_cases=customer_cases,
        )
    except Exception as exc:
        logger.warning("Suggestion agent failed, using fallback: %s: %s", type(exc).__name__, exc)
        body = {
            "suggestions": get_fallback_suggestions()[:max_suggestions],
            "error": str(exc) or type(exc).__name__,
            "fallback": True,
            "timings": {},
        }
    body["timings"]["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    logger.info(
        "[Suggestion] %s suggestions in %sms (cases=%s)",
        len(body["suggestions"]),
        body["timings"]["total_ms"],
        len(customer_cases or []),
    )
    return body


@router.post("/suggest")
async def suggest(req: SuggestRequest) -> Dict[str, Any]:
    return await compute_suggestions(
        req.context,
        req.max_suggestions or cfg.SUGGESTION_MAX,
        customer_profile=req.customer_profile,
        customer_cases=[c.model_dump() for c in req.customer_history or []],
    )
