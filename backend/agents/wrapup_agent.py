"""Wrap-up agent: after the call, turns the transcript into a case note, agreed actions, a follow-up
and a message to the caller, in one LLM round trip. Staff review it and save it (backend/case_store.py).
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from .. import clock, issue_guides
from .. import config as cfg
from ..customer_history import (
    caller_line,
    format_customer_record,
    is_open_case_status,
    verified_case_ids,
)
from ..llm import (
    get_async_llm_client,
    get_wrapup_model,
    llm_extra_params,
    strip_code_fences,
)
from ..prompt_loader import system_prompt, user_prompt
from ..text_guard import ForeignScriptError, contains_foreign_script

logger = logging.getLogger(__name__)

CASE_STATUSES = (
    "Open",
    "Advice given",
    "Pending documents",
    "Claim to be filed",
    "Referred",
    "Resolved",
)
DEFAULT_FOLLOW_UP_DAYS = 7


async def generate_wrapup(
    conversation_transcript: str,
    customer_profile: dict[str, Any] | None = None,
    customer_cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The validated wrap-up (see validate_wrapup). Raises on LLM / parse failure."""
    client = get_async_llm_client()
    if not client:
        raise ValueError("LLM client is not configured")
    profile = customer_profile or {}
    today = clock.today()
    response = await client.chat.completions.create(
        model=get_wrapup_model(),
        temperature=0.2,
        max_completion_tokens=cfg.WRAPUP_MAX_TOKENS,
        timeout=cfg.WRAPUP_TIMEOUT_SECONDS,
        response_format={"type": "json_object"},
        messages=[
            # The guide sits in the system prompt so the provider's prompt cache covers it.
            {"role": "system", "content": f"{system_prompt('wrapup')}\n\n{issue_guides.render()}"},
            {
                "role": "user",
                "content": user_prompt(
                    "wrapup",
                    today=clock.today_label(),
                    caller=caller_line(profile),
                    customer_record=format_customer_record(profile, customer_cases, today),
                    conversation_transcript=conversation_transcript,
                ),
            },
        ],
        **llm_extra_params(get_wrapup_model()),
    )
    parsed = json.loads(strip_code_fences(response.choices[0].message.content or ""))
    wrapup = validate_wrapup(parsed, profile, customer_cases, today)
    if contains_foreign_script(wrapup):
        raise ForeignScriptError("Wrap-up contained non-English text")
    return wrapup


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if isinstance(value, (str, int, float)) else ""


def _iso_date(value: Any) -> str:
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except (TypeError, ValueError):
        return ""


def validate_wrapup(
    raw: Any,
    profile: dict[str, Any],
    cases: list[dict[str, Any]] | None,
    today: date,
) -> dict[str, Any]:
    """Normalise the model's wrap-up. An update must name an open, verified case; otherwise it is new."""
    raw = raw if isinstance(raw, dict) else {}
    case_raw = raw.get("case") if isinstance(raw.get("case"), dict) else {}
    issue_type = _text(raw.get("issue_type")) or "Other"

    open_ids = {
        str(c.get("case_id"))
        for c in cases or []
        if isinstance(c, dict) and is_open_case_status(c.get("status"))
    } & verified_case_ids(profile, cases)
    case_id = _text(case_raw.get("case_id"))
    updating = case_raw.get("action") == "update" and case_id in open_ids
    status = _text(case_raw.get("status"))
    case = {
        "action": "update" if updating else "new",
        "case_id": case_id if updating else None,
        "case_type": _text(case_raw.get("case_type")) or issue_type,
        "company": _text(case_raw.get("company")),
        "status": status if status in CASE_STATUSES else "Open",
    }

    actions = []
    for item in raw.get("actions") if isinstance(raw.get("actions"), list) else []:
        if isinstance(item, dict) and _text(item.get("action")):
            actions.append(
                {
                    "owner": "caller" if item.get("owner") == "caller" else "centre",
                    "action": _text(item.get("action")),
                    "due": _iso_date(item.get("due")),
                }
            )
    actions = actions[:5]

    follow_raw = raw.get("follow_up") if isinstance(raw.get("follow_up"), dict) else {}
    follow_date = _iso_date(follow_raw.get("date"))
    if not follow_date or follow_date < today.isoformat():
        follow_date = (today + timedelta(days=DEFAULT_FOLLOW_UP_DAYS)).isoformat()
    has_phone = bool(profile.get("contact_number"))
    channel = _text(follow_raw.get("channel")).lower()
    follow_up = {
        "date": follow_date,
        "channel": (channel if channel in ("phone", "email", "sms") else "phone" if has_phone else "email"),
        "reason": _text(follow_raw.get("reason")),
    }

    msg_raw = raw.get("message_to_caller") if isinstance(raw.get("message_to_caller"), dict) else {}
    msg_channel = "email" if profile.get("email") else "sms"
    body = str(msg_raw.get("body") or "").strip()
    message = {
        "channel": msg_channel,
        "subject": _text(msg_raw.get("subject")) if msg_channel == "email" else "",
        "body": body,
    }

    return {
        "summary": _text(raw.get("summary")),
        "issue_type": issue_type,
        "case": case,
        "actions": actions,
        "next_action": _text(raw.get("next_action")) or (actions[0]["action"] if actions else ""),
        "follow_up": follow_up,
        "message_to_caller": message,
    }
