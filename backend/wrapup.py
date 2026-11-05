"""End of call: POST /wrapup drafts the case note, actions, follow-up and message to the caller;
POST /cases saves the reviewed wrap-up to the case system (demo DB, when CASE_STORE_ENABLED).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from . import case_store
from . import config as cfg
from .agents import wrapup_agent
from .customer_history import case_rows
from .orchestrator import MAX_TURNS, AssistTurn, HistoryState, format_transcript
from .profile import Profile

logger = logging.getLogger(__name__)

router = APIRouter()


class WrapUpRequest(BaseModel):
    turns: list[AssistTurn]
    customer: dict[str, str] = {}
    history: HistoryState | None = None


class SaveCaseRequest(BaseModel):
    customer: dict[str, str] = {}
    wrapup: dict[str, Any]


@router.post("/wrapup")
async def wrap_up(req: WrapUpRequest) -> dict[str, Any]:
    transcript = format_transcript(req.turns[-MAX_TURNS:])
    if len(transcript) < 20:
        return {
            "status": "error",
            "message": "There is not enough conversation to wrap up.",
        }
    history = req.history or HistoryState()
    profile = Profile.from_request(req.customer, {}).suggestion_payload(history.match_strategy)
    cases = case_rows([c.model_dump() for c in history.cases])
    try:
        wrapup = await wrapup_agent.generate_wrapup(transcript, profile, cases)
    except Exception as exc:
        logger.warning("[wrapup] failed: %s: %s", type(exc).__name__, exc)
        return {
            "status": "error",
            "message": "The wrap-up could not be drafted. Please try again.",
        }
    return {"status": "ok", "wrapup": wrapup}


# Plain `def`: FastAPI runs it in the threadpool so blocking DB I/O never stalls the event loop.
@router.post("/cases")
def save_case(req: SaveCaseRequest) -> dict[str, Any]:
    if not cfg.CASE_STORE_ENABLED:
        return {
            "status": "disabled",
            "message": "Saving cases is turned off on this backend (CASE_STORE_ENABLED).",
        }
    try:
        saved = case_store.save(req.customer, req.wrapup)
    except ValueError as exc:
        return {"status": "invalid", "message": str(exc)}
    except Exception as exc:
        logger.exception("[cases] save failed: %s", exc)
        return {
            "status": "error",
            "message": "The case could not be saved. Please try again.",
        }
    logger.info("[cases] %s %s for %s", saved["action"], saved["case_id"], saved["customer_id"])
    return {"status": "saved", **saved}
