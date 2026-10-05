"""The centre's local date, used for deadlines and follow-up dates (the server itself may run on UTC)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import config as cfg


def today() -> date:
    return datetime.now(ZoneInfo(cfg.CENTRE_TIMEZONE)).date()


def today_label() -> str:
    """e.g. "Monday 2026-10-05"."""
    d = today()
    return f"{d:%A} {d.isoformat()}"
