from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Dict, Any
from .suggestions_core import compute_suggestions
from .persistence import persist_suggestion_event
from .auth import enforce_usage_limits

router = APIRouter()


class SuggestRequest(BaseModel):
    context: str
    max_suggestions: int = 2
    session_id: str | None = Field(
        None, description="Persist to this session when valid and owned"
    )


@router.post("/suggest")
async def suggest(
    req: SuggestRequest,
    user_key: str = Depends(enforce_usage_limits),
) -> Dict[str, Any]:
    body = await compute_suggestions(req.context, req.max_suggestions)
    err = body.get("error") if isinstance(body.get("error"), str) else None
    persist_suggestion_event(
        req.session_id,
        user_key,
        req.context,
        body,
        error=err,
    )
    return body
