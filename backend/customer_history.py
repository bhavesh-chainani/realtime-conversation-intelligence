from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from . import config as cfg

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


NO_CUSTOMER_RECORD = "CUSTOMER RECORD: not yet retrieved. Do not mention or guess at prior cases."

# Statuses that mean a case needs no further action; anything else counts as open.
CLOSED_CASE_STATUSES = {"resolved", "closed", "approved", "withdrawn", "completed"}


def is_open_case_status(status: str | None) -> bool:
    return (status or "").strip().lower() not in CLOSED_CASE_STATUSES


def verified_case_ids(
    customer_profile: dict[str, Any] | None, customer_cases: list[dict[str, Any]] | None
) -> set[str]:
    """Case IDs the model may cite. A name-only match is unverified, so none until the NRIC matches."""
    if (customer_profile or {}).get("record_match") == "name":
        return set()
    return {
        str(c.get("case_id")).strip()
        for c in (customer_cases or [])
        if isinstance(c, dict) and c.get("case_id")
    }


def format_customer_record(profile: dict[str, Any] | None, cases: list[dict[str, Any]] | None) -> str:
    """Render verified customer data + prior cases as a compact prompt block."""
    profile = profile or {}
    cases = [c for c in (cases or []) if isinstance(c, dict) and c.get("case_id")]
    if not cases:
        return NO_CUSTOMER_RECORD

    ident = " | ".join(
        f"{label}: {profile[key]}"
        for key, label in (("name", "Name"), ("nric_worker_permit_id", "NRIC"))
        if profile.get(key)
    )
    open_count = sum(1 for c in cases if is_open_case_status(c.get("status")))
    if profile.get("record_match") == "name":
        header = (
            "CUSTOMER RECORD (possible match by NAME ONLY - identity NOT verified yet; "
            "ask for NRIC / FIN before discussing any case details):"
        )
    else:
        header = "CUSTOMER RECORD (verified from the case system, matched on NRIC):"
    lines = [header]
    if ident:
        lines.append(ident)
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
                )
                if part
            )
        )
    return "\n".join(lines)


class CustomerHistoryLookupRequest(BaseModel):
    name: str | None = None
    nric_worker_permit_id: str | None = None


class CustomerHistoryService:
    """Read-only customer history lookup against a curated Postgres view."""

    _relation_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def is_configured(self) -> bool:
        return bool(cfg.CUSTOMER_HISTORY_DATABASE_URL and cfg.CUSTOMER_HISTORY_VIEW and psycopg)

    def lookup(self, name: str | None, nric_worker_permit_id: str | None) -> dict[str, Any]:
        clean_name = self._clean_value(name)
        clean_id = self._clean_value(nric_worker_permit_id)

        if not clean_name and not clean_id:
            logger.info("[customer-history] invalid input: missing name and id")
            return self._response(
                status="invalid_input",
                success=False,
                found=False,
                message="Enter a customer name or NRIC / Work Permit ID to search history.",
                history_summary="",
                cases=[],
                customer={"name": None, "nric_worker_permit_id": None},
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
                customer={
                    "name": clean_name or None,
                    "nric_worker_permit_id": clean_id or None,
                },
            )

        try:
            rows, matched_on = self._query_rows(clean_id, clean_name)
            # Open cases first: they matter most to the operator and the prompt.
            rows = sorted(rows, key=lambda r: not is_open_case_status(r.get("case_status")))
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
                    customer={
                        "name": clean_name or None,
                        "nric_worker_permit_id": clean_id or None,
                    },
                    message="No prior cases found for the provided details.",
                    history_summary="No prior cases found for the provided details.",
                    cases=[],
                )

            customer_name = self._first_non_empty(
                *(row.get("customer_name") for row in rows), fallback=clean_name
            )
            customer_id = self._first_non_empty(
                *(row.get("nric_worker_permit_id") for row in rows), fallback=clean_id
            )
            summary = self._build_summary(customer_name, customer_id, matched_on, cases)
            customer: dict[str, Any] = {
                "name": customer_name or None,
                "nric_worker_permit_id": customer_id or None,
            }
            for column in self._extra_columns():
                customer[column] = self._first_non_empty(*(row.get(column) for row in rows)) or None

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
                customer={
                    "name": clean_name or None,
                    "nric_worker_permit_id": clean_id or None,
                },
                error=str(exc),
            )

    def _query_rows(self, clean_id: str, clean_name: str) -> tuple[list[dict[str, Any]], str]:
        relation = self._relation_sql(cfg.CUSTOMER_HISTORY_VIEW)
        max_rows = max(1, int(cfg.CUSTOMER_HISTORY_MAX_ROWS))
        columns = sql.SQL(", ").join(
            sql.Identifier(col)
            for col in (
                "customer_name",
                "nric_worker_permit_id",
                "case_id",
                "company",
                "case_type",
                "case_status",
                "case_summary",
                *self._extra_columns(),
            )
        )

        with self._connect() as conn:
            with conn.cursor() as cur:
                if clean_id:
                    logger.info("[customer-history] attempting id lookup")
                    cur.execute(
                        sql.SQL(
                            """
                            SELECT {columns}
                            FROM {relation}
                            WHERE UPPER(REPLACE(nric_worker_permit_id, ' ', '')) = UPPER(REPLACE(%s, ' ', ''))
                            LIMIT %s
                            """
                        ).format(columns=columns, relation=relation),
                        (clean_id, max_rows),
                    )
                    rows = cur.fetchall()
                    if rows:
                        return rows, "nric_worker_permit_id"

                if clean_name:
                    logger.info("[customer-history] attempting name lookup")
                    cur.execute(
                        sql.SQL(
                            """
                            SELECT {columns}
                            FROM {relation}
                            WHERE LOWER(TRIM(customer_name)) = LOWER(TRIM(%s))
                            LIMIT %s
                            """
                        ).format(columns=columns, relation=relation),
                        (clean_name, max_rows),
                    )
                    rows = cur.fetchall()
                    if rows:
                        return rows, "name"

        attempted = "nric_worker_permit_id" if clean_id else "name"
        return [], attempted

    def _connect(self):
        if psycopg is None or dict_row is None:
            raise RuntimeError("psycopg is not installed")

        timeout_ms = max(100, int(cfg.CUSTOMER_HISTORY_QUERY_TIMEOUT_MS))
        return psycopg.connect(
            cfg.CUSTOMER_HISTORY_DATABASE_URL,
            autocommit=True,
            row_factory=dict_row,
            options=(f"-c default_transaction_read_only=on -c statement_timeout={timeout_ms}"),
        )

    def _extra_columns(self) -> list[str]:
        """Validated optional columns (e.g. address) configured for this deployment."""
        return [col for col in cfg.CUSTOMER_HISTORY_EXTRA_COLUMNS if self._relation_pattern.match(col)]

    def _relation_sql(self, relation: str):
        if sql is None:
            raise RuntimeError("psycopg SQL helpers are unavailable")

        parts = [part.strip() for part in relation.split(".") if part.strip()]
        if not parts or any(not self._relation_pattern.match(part) for part in parts):
            raise ValueError("CUSTOMER_HISTORY_VIEW must be a simple schema-qualified identifier")
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
        self,
        customer_name: str,
        customer_id: str,
        matched_on: str,
        cases: list[dict[str, str]],
    ) -> str:
        label = customer_name or "Customer"
        if customer_id:
            label = f"{label} ({customer_id})"

        base = f"{label} has {len(cases)} matching case{'s' if len(cases) != 1 else ''} in customer history."
        if matched_on == "nric_worker_permit_id":
            base += " Matched on NRIC / Work Permit ID."
        elif matched_on == "name":
            base += " Matched on customer name."

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
    return customer_history_service.lookup(req.name, req.nric_worker_permit_id)
