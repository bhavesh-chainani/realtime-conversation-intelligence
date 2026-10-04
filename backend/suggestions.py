from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Any, Dict, List
from .suggestions_core import compute_suggestions
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
    customer_profile: Dict[str, Any] | None = Field(
        None, description="Verified/extracted customer fields to ground suggestions"
    )
    customer_history: List[CustomerCase] | None = Field(
        None, description="Prior cases from the customer history lookup"
    )


@router.post("/suggest")
async def suggest(req: SuggestRequest) -> Dict[str, Any]:
    return await compute_suggestions(
        req.context,
        req.max_suggestions or cfg.SUGGESTION_MAX,
        customer_profile=req.customer_profile,
        customer_cases=[c.model_dump() for c in req.customer_history or []],
    )
