from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from typing import Any, Dict, List
from .suggestions_core import compute_suggestions
from .persistence import persist_suggestion_event
from .auth import enforce_usage_limits
from . import config as cfg

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
    session_id: str | None = Field(
        None, description="Persist to this session when valid and owned"
    )
    customer_profile: Dict[str, Any] | None = Field(
        None, description="Verified/extracted customer fields to ground suggestions"
    )
    customer_history: List[CustomerCase] | None = Field(
        None, description="Prior cases from the customer history lookup"
    )
    scenario_id: str | None = Field(None, description="Demo scenario (logging only)")
    script_step: str | None = Field(None, description="Demo script line (logging only)")


@router.post("/suggest")
async def suggest(
    req: SuggestRequest,
    background_tasks: BackgroundTasks,
    user_key: str = Depends(enforce_usage_limits),
) -> Dict[str, Any]:
    body = await compute_suggestions(
        req.context,
        req.max_suggestions or cfg.SUGGESTION_MAX,
        customer_profile=req.customer_profile,
        customer_cases=[c.model_dump() for c in req.customer_history or []],
    )
    err = body.get("error") if isinstance(body.get("error"), str) else None
    # Persist after the response is sent so storage I/O never adds latency.
    background_tasks.add_task(
        persist_suggestion_event,
        req.session_id,
        user_key,
        req.context,
        body,
        error=err,
    )
    return body
