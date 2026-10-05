"""Issue guides: what the centre advises per issue type (deciding facts, route, deadline, documents).

Read once at startup from the customer DB (ISSUE_GUIDE_VIEW) and given to the suggestion and wrap-up
agents as a SERVICE GUIDE block, so advice names the right channel for the caller's issue.
"""

from __future__ import annotations

import logging

from . import config as cfg
from . import db
from .db import sql

logger = logging.getLogger(__name__)

GUIDE_FIELDS = (
    "issue_type",
    "applies_when",
    "facts_to_gather",
    "documents",
    "route",
    "deadline",
    "follow_up",
)
NO_GUIDE = (
    "SERVICE GUIDE: none. Give only general next steps; do not name specific forms, agencies or deadlines."
)

_guides: list[dict[str, str]] = []


def load() -> int:
    """Read the guides into memory; returns how many. A missing view leaves the agents without a guide."""
    global _guides
    if not db.is_configured() or not cfg.ISSUE_GUIDE_VIEW:
        return 0
    try:
        query = sql.SQL("SELECT {columns} FROM {relation} ORDER BY issue_type").format(
            columns=sql.SQL(", ").join(sql.Identifier(f) for f in GUIDE_FIELDS),
            relation=db.relation_sql(cfg.ISSUE_GUIDE_VIEW),
        )
        with db.connect() as conn:
            rows = conn.execute(query).fetchall()
    except Exception as exc:
        logger.warning("[issue-guides] not loaded: %s: %s", type(exc).__name__, exc)
        return 0
    _guides = [{f: str(row.get(f) or "").strip() for f in GUIDE_FIELDS} for row in rows]
    logger.info("[issue-guides] loaded %s guides", len(_guides))
    return len(_guides)


def guides() -> list[dict[str, str]]:
    return list(_guides)


def render(items: list[dict[str, str]] | None = None) -> str:
    """The guides as a compact prompt block."""
    items = _guides if items is None else items
    if not items:
        return NO_GUIDE
    lines = [
        "SERVICE GUIDE (the centre's advice per issue type; use the entry that fits the caller's issue):"
    ]
    for g in items:
        lines += [
            f"## {g.get('issue_type')} (when: {g.get('applies_when')})",
            f"- Facts that decide the route: {g.get('facts_to_gather')}",
            f"- Route: {g.get('route')}",
            f"- Deadline: {g.get('deadline')}",
            f"- Documents: {g.get('documents')}",
            f"- Follow-up: {g.get('follow_up')}",
        ]
    return "\n".join(lines)
