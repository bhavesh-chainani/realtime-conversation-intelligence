"""Saves a call's wrap-up as a case, so the next call from the same caller finds it.

Demo only: writes to the demo DB tables (CASE_STORE_CUSTOMERS_TABLE / CASE_STORE_CASES_TABLE) and only
when CASE_STORE_ENABLED. The live history lookup (customer_history.py) stays read-only.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from . import clock, db
from . import config as cfg
from .db import sql
from .quick_entities import normalize_email, normalize_phone


def next_id(existing: list[str], prefix: str, width: int, floor: int = 0) -> str:
    """The next ID after the largest trailing number in `existing`, e.g. CASE-2026-10422."""
    numbers = [int(m.group(1)) for i in existing if (m := re.search(r"(\d+)$", i or ""))]
    return f"{prefix}{max([floor, *numbers]) + 1:0{width}d}"


def format_phone(digits: str) -> str:
    return f"{digits[:4]} {digits[4:]}"


def _ids(conn, table, column: str) -> list[str]:
    query = sql.SQL("SELECT {col} FROM {table}").format(col=sql.Identifier(column), table=table)
    return [row[column] for row in conn.execute(query).fetchall()]


def _find_or_create_customer(conn, table, name: str, phone: str | None, email: str | None):
    """(customer_id, created). Matches on phone, then email, like the history lookup."""
    for condition, value in ((db.PHONE_MATCH, phone), (db.EMAIL_MATCH, email)):
        if not value:
            continue
        row = conn.execute(
            sql.SQL("SELECT customer_id FROM {table} WHERE " + condition + " LIMIT 1").format(table=table),
            (value,),
        ).fetchone()
        if row:
            # Fill in a contact detail the record did not have yet.
            conn.execute(
                sql.SQL(
                    "UPDATE {table} SET contact_number = COALESCE(contact_number, %s), "
                    "email = COALESCE(email, %s) WHERE customer_id = %s"
                ).format(table=table),
                (format_phone(phone) if phone else None, email, row["customer_id"]),
            )
            return row["customer_id"], False
    customer_id = next_id(_ids(conn, table, "customer_id"), "CUST-", 4)
    conn.execute(
        sql.SQL(
            "INSERT INTO {table} (customer_id, customer_name, contact_number, email) VALUES (%s, %s, %s, %s)"
        ).format(table=table),
        (
            customer_id,
            name or "Unknown caller",
            format_phone(phone) if phone else None,
            email,
        ),
    )
    return customer_id, True


def save(customer: dict[str, Any], wrapup: dict[str, Any], today: date | None = None) -> dict:
    """Create or update the case from a reviewed wrap-up. Raises ValueError on unusable input."""
    today = today or clock.today()
    phone = normalize_phone(customer.get("contact_number"))
    email = normalize_email(customer.get("email"))
    if not phone and not email:
        raise ValueError("A contact number or email is needed to save the case.")
    name = " ".join(str(customer.get("name") or "").split())
    case = wrapup.get("case") if isinstance(wrapup.get("case"), dict) else {}
    follow_up = wrapup.get("follow_up") if isinstance(wrapup.get("follow_up"), dict) else {}
    summary = " ".join(str(wrapup.get("summary") or "").split())
    next_action = " ".join(str(wrapup.get("next_action") or "").split()) or None
    status = str(case.get("status") or "Open")
    due = follow_up.get("date") or None

    customers = db.relation_sql(cfg.CASE_STORE_CUSTOMERS_TABLE)
    cases = db.relation_sql(cfg.CASE_STORE_CASES_TABLE)
    with db.connect(read_only=False) as conn, conn.transaction():
        customer_id, customer_created = _find_or_create_customer(conn, customers, name, phone, email)
        if case.get("action") == "update" and case.get("case_id"):
            row = conn.execute(
                sql.SQL(
                    "UPDATE {table} SET case_status = %s, next_action = %s, follow_up_due = %s, "
                    "case_summary = CONCAT_WS(' ', case_summary, %s::text) "
                    "WHERE case_id = %s AND customer_id = %s RETURNING case_id"
                ).format(table=cases),
                (
                    status,
                    next_action,
                    due,
                    f"Update {today}: {summary}",
                    case["case_id"],
                    customer_id,
                ),
            ).fetchone()
            if row:
                return {
                    "case_id": row["case_id"],
                    "customer_id": customer_id,
                    "action": "updated",
                    "customer_created": customer_created,
                }
        # Above every seeded ID, so new cases sort first in the history view.
        case_id = next_id(_ids(conn, cases, "case_id"), f"CASE-{today.year}-", 5)
        conn.execute(
            sql.SQL(
                "INSERT INTO {table} (case_id, customer_id, company, case_type, case_status, "
                "case_summary, opened_on, next_action, follow_up_due) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            ).format(table=cases),
            (
                case_id,
                customer_id,
                str(case.get("company") or "") or None,
                str(case.get("case_type") or wrapup.get("issue_type") or "General enquiry"),
                status,
                summary,
                today,
                next_action,
                due,
            ),
        )
    return {
        "case_id": case_id,
        "customer_id": customer_id,
        "action": "created",
        "customer_created": customer_created,
    }
