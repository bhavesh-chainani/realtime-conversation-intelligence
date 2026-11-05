"""Postgres connections to the customer DB, shared by the history lookup, issue guides and case store."""

from __future__ import annotations

import re

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from . import config as cfg

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# How a caller matches a stored customer (used by the history lookup and the case store): phones on
# their last 8 digits, so "+65 9123 4567" matches "91234567"; emails case-insensitively.
PHONE_MATCH = "RIGHT(REGEXP_REPLACE(contact_number, '\\D', '', 'g'), 8) = %s"
EMAIL_MATCH = "LOWER(TRIM(email)) = %s"


def is_configured() -> bool:
    return bool(cfg.CUSTOMER_HISTORY_DATABASE_URL)


def connect(read_only: bool = True) -> psycopg.Connection:
    """A dict-row connection with a statement timeout. Read-only unless a write is intended."""
    options = f"-c statement_timeout={max(100, cfg.CUSTOMER_HISTORY_QUERY_TIMEOUT_MS)}"
    if read_only:
        options = f"-c default_transaction_read_only=on {options}"
    return psycopg.connect(
        cfg.CUSTOMER_HISTORY_DATABASE_URL, autocommit=True, row_factory=dict_row, options=options
    )


def relation_sql(relation: str) -> sql.Composed:
    """A schema-qualified table or view name as safe SQL; raises on anything but plain identifiers."""
    parts = [part.strip() for part in relation.split(".") if part.strip()]
    if not parts or any(not IDENTIFIER.match(part) for part in parts):
        raise ValueError(f"{relation!r} must be a simple schema-qualified identifier")
    return sql.SQL(".").join(sql.Identifier(part) for part in parts)
