"""Session event persistence helpers (used by sync API + async worker)."""

from __future__ import annotations

import logging
from typing import Any

from .session_store import SESSION_STORE

logger = logging.getLogger(__name__)


def persist_suggestion_event(
    session_id: str | None,
    user_key: str,
    transcript: str,
    response: dict[str, Any],
    error: str | None = None,
) -> None:
    if not session_id:
        return
    try:
        if not SESSION_STORE.ensure_session_owned(session_id, user_key):
            logger.warning(
                "[persist] suggest skipped: session_id not owned session_id=%s",
                session_id,
            )
            return
        SESSION_STORE.update_transcript_snapshot(session_id, user_key, transcript)
        SESSION_STORE.append_event(
            session_id,
            user_key,
            "suggestions.response",
            {"response": response, "error": error},
        )
    except Exception as exc:
        logger.warning("[persist] suggest failed: %s", exc)


def persist_customer_extract_event(
    session_id: str | None,
    user_key: str,
    transcript: str,
    response: dict[str, Any],
    error: str | None = None,
) -> None:
    if not session_id:
        return
    try:
        if not SESSION_STORE.ensure_session_owned(session_id, user_key):
            logger.warning(
                "[persist] extract skipped: session_id not owned session_id=%s",
                session_id,
            )
            return
        SESSION_STORE.update_transcript_snapshot(session_id, user_key, transcript)
        SESSION_STORE.append_event(
            session_id,
            user_key,
            "customer_data.extract",
            {"response": response, "error": error},
        )
    except Exception as exc:
        logger.warning("[persist] extract failed: %s", exc)


def persist_customer_history_lookup_event(
    session_id: str | None,
    user_key: str,
    lookup_inputs: dict[str, Any],
    response: dict[str, Any],
    error: str | None = None,
) -> None:
    if not session_id:
        return
    try:
        if not SESSION_STORE.ensure_session_owned(session_id, user_key):
            logger.warning(
                "[persist] customer history skipped: session_id not owned session_id=%s",
                session_id,
            )
            return
        SESSION_STORE.append_event(
            session_id,
            user_key,
            "customer_history.lookup",
            {"lookup": lookup_inputs, "response": response, "error": error},
        )
    except Exception as exc:
        logger.warning("[persist] customer history failed: %s", exc)
