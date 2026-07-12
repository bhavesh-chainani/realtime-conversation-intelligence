from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .auth import enforce_usage_limits
from .session_store import SESSION_STORE

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("/")
async def create_session(
    user_key: str = Depends(enforce_usage_limits),
) -> dict[str, Any]:
    session_id = SESSION_STORE.create_session(user_key)
    return {"session_id": session_id, "user_key": user_key}


@router.get("/{session_id}")
async def get_session(
    session_id: str, user_key: str = Depends(enforce_usage_limits)
) -> dict[str, Any]:
    meta = SESSION_STORE.get_session(session_id, user_key)
    if not meta:
        raise HTTPException(status_code=404, detail="Session not found")
    events = SESSION_STORE.list_recent_events(session_id, user_key, limit=50)
    return {**meta, "recent_events": events}
