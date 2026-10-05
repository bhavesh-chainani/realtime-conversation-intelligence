"""Customer DB lookup (read-only Postgres view) and how the record is shown to the suggestion agent."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from . import config as cfg
from . import db
from .db import sql
from .profile import identity_key, next_lookup, records_prefill
from .quick_entities import normalize_email, normalize_phone

logger = logging.getLogger(__name__)

router = APIRouter()


class CustomerCase(BaseModel):
    """A prior case as the UI and the prompts use it (dates as YYYY-MM-DD strings)."""

    case_id: str
    company: str | None = None
    type: str | None = None
    status: str | None = None
    summary: str | None = None
    opened_on: str | None = None
    next_action: str | None = None
    follow_up_due: str | None = None


CASE_FIELDS = tuple(CustomerCase.model_fields)
# View column for each case field.
CASE_COLUMNS = {
    "case_id": "case_id",
    "company": "company",
    "type": "case_type",
    "status": "case_status",
    "summary": "case_summary",
    "opened_on": "opened_on",
    "next_action": "next_action",
    "follow_up_due": "follow_up_due",
}


def case_rows(rows: list[Any] | None) -> list[dict[str, str]]:
    """Cases as plain dicts with every field a string ("" when missing)."""
    return [{k: str(row.get(k) or "") for k in CASE_FIELDS} for row in rows or [] if isinstance(row, dict)]


NO_CUSTOMER_RECORD = "CUSTOMER RECORD: none. Do not mention or guess at prior cases."

CARD_FIELDS = (
    ("name", "Name"),
    ("contact_number", "Contact number"),
    ("email", "Email"),
    ("purpose_of_call", "Purpose of call"),
)

MATCH_LABELS = {
    "contact_number": "contact number",
    "email": "email",
    "name": "customer name",
}

# Statuses that mean a case needs no further action; anything else counts as open.
CLOSED_CASE_STATUSES = {"resolved", "closed", "approved", "withdrawn", "completed"}


def is_open_case_status(status: str | None) -> bool:
    return (status or "").strip().lower() not in CLOSED_CASE_STATUSES


def verified_case_ids(
    customer_profile: dict[str, Any] | None, customer_cases: list[dict[str, Any]] | None
) -> set[str]:
    """Case IDs the model may cite. A name-only match is unverified, so none until a phone or email matches."""
    if (customer_profile or {}).get("record_match") == "name":
        return set()
    return {
        str(c.get("case_id")).strip()
        for c in (customer_cases or [])
        if isinstance(c, dict) and c.get("case_id")
    }


def _missing_identity(profile: dict[str, Any]) -> list[str]:
    """Identity details still to ask for: the history check uses the contact number and email."""
    missing = [] if profile.get("name") else ["full name"]
    for key, label, valid in (
        ("contact_number", "contact number", normalize_phone),
        ("email", "email address", normalize_email),
    ):
        if not profile.get(key):
            missing.append(label)
        elif not valid(profile[key]):
            missing.append(f"a complete {label} (the one on the card looks incomplete)")
    return missing


def _joined(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def caller_line(profile: dict[str, Any]) -> str:
    """The caller card as one line, e.g. "Name: Ahmad Rahim | Contact number: 81112222 | ..."."""
    return " | ".join(f"{label}: {profile.get(key) or 'not given'}" for key, label in CARD_FIELDS)


def format_caller_card(profile: dict[str, Any] | None) -> str:
    """What Staff already know about the caller, and where the history check stands.

    Every state names the identity details still missing, so the agent asks for them together, once.
    """
    profile = profile or {}
    known = caller_line(profile)
    missing = _missing_identity(profile)
    ask = f"Ask for the caller's {_joined(missing)} in one question" if missing else ""
    has_contact = bool(
        normalize_phone(profile.get("contact_number")) or normalize_email(profile.get("email"))
    )
    status = profile.get("lookup_status") or "not_started"
    if status == "pending":
        check = "in progress. Do not mention prior cases yet. " + (
            f"{ask}." if missing else "Do not ask for more identity details."
        )
    elif status == "verified":
        matched_on = MATCH_LABELS.get(profile.get("record_match"), "contact details")
        check = f"done, verified (matched on {matched_on}). Do not ask for identity details again."
    elif status == "name":
        needs = [m for m in missing if m != "full name"] or ["contact number"]
        check = (
            "possible match by NAME ONLY, not verified. Ask for the caller's "
            f"{_joined(needs)} to confirm before discussing any case details."
        )
    elif status == "not_found" and has_contact:
        check = "done, no prior cases found for the details given. " + (
            f"{ask}, to check once more and complete the record."
            if missing
            else "Treat this as a new caller and do not ask for more identity details."
        )
    elif status == "not_found":
        check = (
            "no record under that name (it may be recorded differently). "
            f"{ask}, to check again."  # without a valid phone / email, both are missing
        )
    elif status == "unavailable":
        check = "the case system could not be checked. Carry on without history." + (
            f" Still {ask[0].lower()}{ask[1:]}, for the record." if missing else ""
        )
    else:
        check = "not done yet. " + (
            f"{ask}, so the history check can run."
            if missing
            else "It runs once a valid contact number or email is given."
        )
    return (
        "CALLER CARD (details Staff already have; never ask for these again):\n"
        f"{known}\nHISTORY CHECK: {check}"
    )


def _follow_up_label(case: dict[str, Any], today: date) -> str:
    """ "follow-up due 2026-10-03 (overdue)" for an open case with a due date, else ""."""
    due = str(case.get("follow_up_due") or "").strip()
    if not due or not is_open_case_status(case.get("status")):
        return ""
    try:
        overdue = date.fromisoformat(due) < today
    except ValueError:
        overdue = False
    return f"follow-up due {due}{' (OVERDUE)' if overdue else ''}"


def format_customer_record(
    profile: dict[str, Any] | None,
    cases: list[dict[str, Any]] | None,
    today: date | None = None,
) -> str:
    """Render verified customer data + prior cases as a compact prompt block."""
    profile = profile or {}
    today = today or date.today()
    cases = [c for c in (cases or []) if isinstance(c, dict) and c.get("case_id")]
    if not cases:
        return NO_CUSTOMER_RECORD

    ident = " | ".join(
        f"{label}: {profile[key]}"
        for key, label in (
            ("name", "Name"),
            ("contact_number", "Phone"),
            ("email", "Email"),
        )
        if profile.get(key)
    )
    open_count = sum(1 for c in cases if is_open_case_status(c.get("status")))
    match = profile.get("record_match")
    if match == "name":
        header = (
            "CUSTOMER RECORD (possible match by NAME ONLY - identity NOT verified yet; "
            "ask for the caller's contact number before discussing any case details):"
        )
    else:
        matched_on = MATCH_LABELS.get(match, "contact details")
        header = f"CUSTOMER RECORD (verified from the case system, matched on {matched_on}):"
    lines = [header]
    if ident:
        lines.append(ident)
    # Overdue actions go first, so the agent raises them before the new issue.
    overdue = [
        f"{c['case_id']}: {c.get('next_action') or 'follow-up'}"
        for c in cases
        if "OVERDUE" in _follow_up_label(c, today)
    ]
    if overdue and match != "name":
        lines.append("OVERDUE ACTIONS TO RAISE FIRST: " + "; ".join(overdue))
    lines.append(f"Prior cases ({len(cases)}; {open_count} open):")
    for c in cases:
        status = str(c.get("status") or "").strip()
        status_label = f"{status.upper()} (open)" if is_open_case_status(status) else status
        lines.append(
            "- "
            + " | ".join(
                part
                for part in (
                    str(c.get("case_id") or "").strip(),
                    str(c.get("company") or "").strip(),
                    str(c.get("type") or "").strip(),
                    status_label,
                    str(c.get("summary") or "").strip(),
                    f"opened {c['opened_on']}" if c.get("opened_on") else "",
                    (
                        f"next: {c['next_action']}"
                        if c.get("next_action") and is_open_case_status(status)
                        else ""
                    ),
                    _follow_up_label(c, today),
                )
                if part
            )
        )
    return "\n".join(lines)


class CustomerHistoryLookupRequest(BaseModel):
    name: str | None = None
    contact_number: str | None = None
    email: str | None = None


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _first(values: Any, fallback: str = "") -> str:
    return next((v for v in map(_clean, values) if v), _clean(fallback))


def _summary(customer_name: str, matched_on: str, cases: list[dict[str, str]]) -> str:
    n = len(cases)
    text = f"{customer_name or 'Customer'} has {n} matching case{'s' if n != 1 else ''} in customer history."
    if matched_on in MATCH_LABELS:
        text += f" Matched on {MATCH_LABELS[matched_on]}."
    return text


class CustomerHistoryService:
    """Read-only customer history lookup against a curated Postgres view.

    Every response has `status` and `summary`; a match ("ok") adds `match_strategy`, `customer`,
    `cases` and `open_count`.
    """

    def is_configured(self) -> bool:
        return bool(db.is_configured() and cfg.CUSTOMER_HISTORY_VIEW)

    def lookup(self, name: str | None, phone: str | None, email: str | None) -> dict[str, Any]:
        clean_name = _clean(name)
        clean_phone = normalize_phone(phone) or ""
        clean_email = normalize_email(email) or ""
        if not clean_name and not clean_phone and not clean_email:
            return {
                "status": "invalid_input",
                "summary": "Enter a customer name, contact number or email to search history.",
                "cases": [],
            }
        if not self.is_configured():
            logger.warning("[customer-history] not configured")
            return {
                "status": "not_configured",
                "summary": "Customer history lookup is not configured on this backend.",
                "cases": [],
            }
        try:
            rows, matched_on = self._query_rows(clean_phone, clean_email, clean_name)
        except Exception as exc:
            logger.exception("[customer-history] lookup failed: %s", exc)
            return {
                "status": "error",
                "summary": "Customer history lookup failed. Please try again.",
                "cases": [],
            }
        logger.info("[customer-history] matched_on=%s rows=%s", matched_on, len(rows))
        if not rows:
            return {
                "status": "not_found",
                "summary": "No prior cases found for the provided details.",
                "cases": [],
            }

        # Open cases first: they matter most to the operator and the prompt.
        rows = sorted(rows, key=lambda r: not is_open_case_status(r.get("case_status")))
        cases = case_rows([{field: row.get(col) for field, col in CASE_COLUMNS.items()} for row in rows])
        customer_name = _first((r.get("customer_name") for r in rows), clean_name)
        if matched_on == "name":
            # Unverified: do not reveal the record's contact details to whoever said the name.
            customer = {"name": customer_name or None, "contact_number": None, "email": None}
        else:
            customer = {
                "name": customer_name or None,
                "contact_number": _first((r.get("contact_number") for r in rows), clean_phone) or None,
                "email": _first((r.get("email") for r in rows), clean_email) or None,
            }
        return {
            "status": "ok",
            "match_strategy": matched_on,
            "customer": customer,
            "cases": cases,
            "open_count": sum(1 for c in cases if is_open_case_status(c["status"])),
            "summary": _summary(customer_name, matched_on, cases),
        }

    def _query_rows(
        self, clean_phone: str, clean_email: str, clean_name: str
    ) -> tuple[list[dict[str, Any]], str]:
        """Rows for the first of phone, email, name that matches, and which one matched.

        Phones are compared on their last 8 digits, so "+65 9123 4567" in the DB matches "91234567".
        """
        relation = db.relation_sql(cfg.CUSTOMER_HISTORY_VIEW)
        columns = sql.SQL(", ").join(
            sql.Identifier(col)
            for col in ("customer_name", "contact_number", "email", *CASE_COLUMNS.values())
        )
        attempts = [
            (strategy, condition, value)
            for strategy, condition, value in (
                ("contact_number", db.PHONE_MATCH, clean_phone),
                ("email", db.EMAIL_MATCH, clean_email),
                ("name", "LOWER(TRIM(customer_name)) = LOWER(TRIM(%s))", clean_name),
            )
            if value
        ]
        with db.connect() as conn:
            for strategy, condition, value in attempts:
                rows = conn.execute(
                    sql.SQL("SELECT {columns} FROM {relation} WHERE " + condition + " LIMIT %s").format(
                        columns=columns, relation=relation
                    ),
                    (value, max(1, cfg.CUSTOMER_HISTORY_MAX_ROWS)),
                ).fetchall()
                if rows:
                    return rows, strategy
        return [], attempts[0][0]


customer_history_service = CustomerHistoryService()


# Plain `def`: FastAPI runs it in the threadpool so blocking DB I/O never stalls the event loop.
@router.post("/customer-history")
def lookup_customer_history(req: CustomerHistoryLookupRequest) -> dict[str, Any]:
    """Manual Look up from the caller card. Live calls look the caller up inside POST /assist."""
    result = customer_history_service.lookup(req.name, req.contact_number, req.email)
    searched = next_lookup(None, req.name or "", req.contact_number or "", req.email or "")
    prefill = records_prefill(result)
    key = searched.key if searched else None
    if prefill:
        # Key on the record's phone / email, which the card will now hold (see orchestrator.finish_lookup).
        key = (
            identity_key(
                prefill.get("contact_number") or req.contact_number, prefill.get("email") or req.email
            )
            or key
        )
    return {**result, "lookup_key": key, "prefill": prefill}
