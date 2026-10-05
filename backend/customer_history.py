"""Customer DB lookup (read-only Postgres view) and how the record is shown to the suggestion agent."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from . import config as cfg
from .profile import identity_key, next_lookup, records_prefill
from .quick_entities import normalize_email, normalize_phone

logger = logging.getLogger(__name__)

try:
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - import guard for optional dependency failures
    psycopg = None
    sql = None
    dict_row = None


router = APIRouter()


class CustomerCase(BaseModel):
    case_id: str
    company: str | None = None
    type: str | None = None
    status: str | None = None
    summary: str | None = None


NO_CUSTOMER_RECORD = "CUSTOMER RECORD: none. Do not mention or guess at prior cases."

CARD_FIELDS = (
    ("name", "Name"),
    ("contact_number", "Contact number"),
    ("email", "Email"),
    ("purpose_of_call", "Purpose of call"),
)

# What the suggestion agent is told about the history check, by orchestrator lookup status.
HISTORY_CHECK = {
    "pending": "in progress. Do not mention prior cases yet, and do not ask for more identity details.",
    "verified": "done, verified (matched on {matched_on}). Do not ask for identity details again.",
    "name": (
        "possible match by NAME ONLY, not verified. Ask for the caller's contact number to confirm "
        "before discussing any case details."
    ),
    "new_caller": (
        "done, no prior cases found. Treat this as a new caller and do not ask for more identity details."
    ),
    "not_found_by_name": (
        "no record under that name. Ask for the caller's contact number to check again "
        "(the name may be recorded differently)."
    ),
    "unavailable": "the case system could not be checked. Carry on without history.",
}

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


def format_caller_card(profile: dict[str, Any] | None) -> str:
    """What Staff already know about the caller, and where the history check stands."""
    profile = profile or {}
    known = " | ".join(
        f"{label}: {profile.get(key) or 'not given'}" for key, label in CARD_FIELDS
    )
    has_contact = bool(
        normalize_phone(profile.get("contact_number"))
        or normalize_email(profile.get("email"))
    )
    status = profile.get("lookup_status") or "not_started"
    if status == "not_found":
        status = "new_caller" if has_contact else "not_found_by_name"
    if status == "not_started":
        if profile.get("contact_number") and not has_contact:
            needs = "a complete contact number (the one on the card looks incomplete)"
        elif profile.get("name"):
            needs = "contact number"
        else:
            needs = "full name and contact number"
        check = f"not done yet. It needs the caller's {needs}."
    else:
        matched_on = MATCH_LABELS.get(profile.get("record_match"), "contact details")
        check = HISTORY_CHECK.get(status, HISTORY_CHECK["unavailable"]).format(
            matched_on=matched_on
        )
    return (
        "CALLER CARD (details Staff already have; never ask for these again):\n"
        f"{known}\nHISTORY CHECK: {check}"
    )


def format_customer_record(
    profile: dict[str, Any] | None, cases: list[dict[str, Any]] | None
) -> str:
    """Render verified customer data + prior cases as a compact prompt block."""
    profile = profile or {}
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
        header = (
            f"CUSTOMER RECORD (verified from the case system, matched on {matched_on}):"
        )
    lines = [header]
    if ident:
        lines.append(ident)
    lines.append(f"Prior cases ({len(cases)}; {open_count} open):")
    for c in cases:
        status = str(c.get("status") or "").strip()
        status_label = (
            f"{status.upper()} (open)" if is_open_case_status(status) else status
        )
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
                )
                if part
            )
        )
    return "\n".join(lines)


class CustomerHistoryLookupRequest(BaseModel):
    name: str | None = None
    contact_number: str | None = None
    email: str | None = None


class CustomerHistoryService:
    """Read-only customer history lookup against a curated Postgres view."""

    _relation_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def is_configured(self) -> bool:
        return bool(
            cfg.CUSTOMER_HISTORY_DATABASE_URL and cfg.CUSTOMER_HISTORY_VIEW and psycopg
        )

    def lookup(
        self, name: str | None, phone: str | None, email: str | None
    ) -> dict[str, Any]:
        clean_name = self._clean_value(name)
        clean_phone = normalize_phone(phone) or ""
        clean_email = normalize_email(email) or ""
        searched = {
            "name": clean_name or None,
            "contact_number": clean_phone or None,
            "email": clean_email or None,
        }

        if not clean_name and not clean_phone and not clean_email:
            logger.info("[customer-history] invalid input: no name, phone or email")
            return self._response(
                status="invalid_input",
                success=False,
                found=False,
                message="Enter a customer name, contact number or email to search history.",
                history_summary="",
                cases=[],
                customer=searched,
            )

        if not self.is_configured():
            logger.warning("[customer-history] not configured")
            return self._response(
                status="not_configured",
                success=False,
                found=False,
                message="Customer history lookup is not configured on this backend.",
                history_summary="",
                cases=[],
                customer=searched,
            )

        try:
            rows, matched_on = self._query_rows(clean_phone, clean_email, clean_name)
            # Open cases first: they matter most to the operator and the prompt.
            rows = sorted(
                rows, key=lambda r: not is_open_case_status(r.get("case_status"))
            )
            cases = [self._format_case(row) for row in rows]
            logger.info(
                "[customer-history] lookup complete matched_on=%s rows=%s",
                matched_on,
                len(cases),
            )

            if not cases:
                return self._response(
                    status="not_found",
                    success=True,
                    found=False,
                    match_strategy=matched_on,
                    customer=searched,
                    message="No prior cases found for the provided details.",
                    history_summary="No prior cases found for the provided details.",
                    cases=[],
                )

            customer_name = self._first_non_empty(
                *(row.get("customer_name") for row in rows), fallback=clean_name
            )
            customer_phone = self._first_non_empty(
                *(row.get("contact_number") for row in rows), fallback=clean_phone
            )
            customer_email = self._first_non_empty(
                *(row.get("email") for row in rows), fallback=clean_email
            )
            summary = self._build_summary(customer_name, matched_on, cases)
            customer: dict[str, Any] = {
                "name": customer_name or None,
                "contact_number": customer_phone or None,
                "email": customer_email or None,
            }
            for column in self._extra_columns():
                customer[column] = (
                    self._first_non_empty(*(row.get(column) for row in rows)) or None
                )
            if matched_on == "name":
                # Unverified: do not reveal the record's contact details to whoever said the name.
                customer = {**searched, "name": customer_name or None}

            return self._response(
                status="ok",
                success=True,
                found=True,
                match_strategy=matched_on,
                customer=customer,
                open_count=sum(1 for c in cases if is_open_case_status(c["status"])),
                companies=sorted({c["company"] for c in cases if c["company"]}),
                message=f"Found {len(cases)} customer history entr{'y' if len(cases) == 1 else 'ies'}.",
                history_summary=summary,
                cases=cases,
            )
        except Exception as exc:
            logger.exception("[customer-history] lookup failed: %s", exc)
            return self._response(
                status="error",
                success=False,
                found=False,
                message="Customer history lookup failed. Please try again.",
                history_summary="",
                cases=[],
                customer=searched,
                error=str(exc),
            )

    def _query_rows(
        self, clean_phone: str, clean_email: str, clean_name: str
    ) -> tuple[list[dict[str, Any]], str]:
        """Rows for the first of phone, email, name that matches, and which one matched.

        Phones are compared on their last 8 digits, so "+65 9123 4567" in the DB matches "91234567".
        """
        relation = self._relation_sql(cfg.CUSTOMER_HISTORY_VIEW)
        max_rows = max(1, int(cfg.CUSTOMER_HISTORY_MAX_ROWS))
        columns = sql.SQL(", ").join(
            sql.Identifier(col)
            for col in (
                "customer_name",
                "contact_number",
                "email",
                "case_id",
                "company",
                "case_type",
                "case_status",
                "case_summary",
                *self._extra_columns(),
            )
        )

        attempts = [
            (strategy, condition, value)
            for strategy, condition, value in (
                (
                    "contact_number",
                    "RIGHT(REGEXP_REPLACE(contact_number, '\\D', '', 'g'), 8) = %s",
                    clean_phone,
                ),
                ("email", "LOWER(TRIM(email)) = %s", clean_email),
                ("name", "LOWER(TRIM(customer_name)) = LOWER(TRIM(%s))", clean_name),
            )
            if value
        ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                for strategy, condition, value in attempts:
                    logger.info("[customer-history] attempting %s lookup", strategy)
                    cur.execute(
                        sql.SQL(
                            "SELECT {columns} FROM {relation} WHERE "
                            + condition
                            + " LIMIT %s"
                        ).format(columns=columns, relation=relation),
                        (value, max_rows),
                    )
                    rows = cur.fetchall()
                    if rows:
                        return rows, strategy

        return [], attempts[0][0]

    def _connect(self):
        if psycopg is None or dict_row is None:
            raise RuntimeError("psycopg is not installed")

        timeout_ms = max(100, int(cfg.CUSTOMER_HISTORY_QUERY_TIMEOUT_MS))
        return psycopg.connect(
            cfg.CUSTOMER_HISTORY_DATABASE_URL,
            autocommit=True,
            row_factory=dict_row,
            options=(
                f"-c default_transaction_read_only=on -c statement_timeout={timeout_ms}"
            ),
        )

    def _extra_columns(self) -> list[str]:
        """Validated optional columns configured for this deployment."""
        return [
            col
            for col in cfg.CUSTOMER_HISTORY_EXTRA_COLUMNS
            if self._relation_pattern.match(col)
        ]

    def _relation_sql(self, relation: str):
        if sql is None:
            raise RuntimeError("psycopg SQL helpers are unavailable")

        parts = [part.strip() for part in relation.split(".") if part.strip()]
        if not parts or any(not self._relation_pattern.match(part) for part in parts):
            raise ValueError(
                "CUSTOMER_HISTORY_VIEW must be a simple schema-qualified identifier"
            )
        return sql.SQL(".").join(sql.Identifier(part) for part in parts)

    def _response(self, **payload: Any) -> dict[str, Any]:
        return payload

    def _format_case(self, row: dict[str, Any]) -> dict[str, str]:
        return {
            "case_id": self._stringify(row.get("case_id")),
            "company": self._stringify(row.get("company")),
            "type": self._stringify(row.get("case_type")),
            "status": self._stringify(row.get("case_status")),
            "summary": self._stringify(row.get("case_summary")),
        }

    def _build_summary(
        self, customer_name: str, matched_on: str, cases: list[dict[str, str]]
    ) -> str:
        label = customer_name or "Customer"
        base = f"{label} has {len(cases)} matching case{'s' if len(cases) != 1 else ''} in customer history."
        if matched_on in MATCH_LABELS:
            base += f" Matched on {MATCH_LABELS[matched_on]}."

        first_summary = self._clean_value(cases[0].get("summary")) if cases else ""
        if first_summary:
            base += f" Example case: {first_summary}"
        return base

    def _clean_value(self, value: Any) -> str:
        return str(value or "").strip()

    def _stringify(self, value: Any) -> str:
        return self._clean_value(value)

    def _first_non_empty(self, *values: Any, fallback: str = "") -> str:
        for value in values:
            cleaned = self._clean_value(value)
            if cleaned:
                return cleaned
        return self._clean_value(fallback)


customer_history_service = CustomerHistoryService()


# Plain `def`: FastAPI runs it in the threadpool so blocking DB I/O never stalls the event loop.
@router.post("/customer-history")
def lookup_customer_history(req: CustomerHistoryLookupRequest) -> dict[str, Any]:
    """Manual Look up from the caller card. Live calls look the caller up inside POST /assist."""
    result = customer_history_service.lookup(req.name, req.contact_number, req.email)
    searched = next_lookup(
        None, req.name or "", req.contact_number or "", req.email or ""
    )
    prefill = records_prefill(result)
    key = searched.key if searched else None
    if prefill:
        # Key on the record's phone / email, which the card will now hold (see orchestrator.finish_lookup).
        key = (
            identity_key(
                prefill.get("contact_number") or req.contact_number,
                prefill.get("email") or req.email,
            )
            or key
        )
    return {**result, "lookup_key": key, "prefill": prefill}
