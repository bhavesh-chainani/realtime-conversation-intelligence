"""Start a local embedded Postgres for demos and seed dummy customer history.

Usage: .venv/bin/python scripts/demo_db.py
Idempotent: safe to re-run; it restarts the server if needed and reseeds the data.
Prints the CUSTOMER_HISTORY_DATABASE_URL to put in .env.
"""

from pathlib import Path

import pgserver
import psycopg

PGDATA = Path(__file__).resolve().parent.parent / "data" / "demo_pg"

# (customer_id, name, contact_number, email). Phones are stored in mixed formats on purpose: the lookup
# compares the last 8 digits, as a real case system will not be consistent either.
CUSTOMERS = [
    ("CUST-0001", "Katherine Liao", "+65 9123 4567", "katherine.liao@example.com"),
    ("CUST-0002", "Rajesh Kumar", "8234 5678", "rajesh.kumar@example.com"),
    ("CUST-0003", "Maria Santos", "+6593456789", "maria.santos@example.com"),
    ("CUST-0004", "David Tan", "9456-7890", "david.tan@example.com"),
    ("CUST-0005", "Nguyen Van An", "8567 8901", "nguyen.vanan@example.com"),
]

# (customer_id, case_id, company, case_type, case_status, case_summary)
CASES = [
    (
        "CUST-0001",
        "CASE-2025-10421",
        "Brightpath Logistics Pte Ltd",
        "Salary dispute",
        "Resolved",
        "Unpaid overtime for March to May 2025. Employer paid SGD 1,840 after mediation.",
    ),
    (
        "CUST-0001",
        "CASE-2026-03117",
        "Brightpath Logistics Pte Ltd",
        "Leave entitlement",
        "Open",
        "Claims annual leave was forfeited without notice. Awaiting employer response.",
    ),
    (
        "CUST-0002",
        "CASE-2025-08833",
        "Harbourline Construction Pte Ltd",
        "Workplace injury",
        "Closed",
        "Hand injury on site in Aug 2025. WICA claim approved and compensation paid.",
    ),
    (
        "CUST-0002",
        "CASE-2026-01954",
        "Harbourline Construction Pte Ltd",
        "Work permit renewal",
        "Pending documents",
        "Renewal submitted Jan 2026. Medical check-up report still outstanding.",
    ),
    (
        "CUST-0002",
        "CASE-2026-04402",
        "Harbourline Construction Pte Ltd",
        "Housing complaint",
        "Under review",
        "Reported overcrowded dormitory conditions. Inspection scheduled.",
    ),
    (
        "CUST-0003",
        "CASE-2025-11760",
        "Evergreen Home Services",
        "Employment transfer",
        "Approved",
        "Transfer to new employer approved in Nov 2025 with previous employer's consent.",
    ),
    (
        "CUST-0004",
        "CASE-2024-06215",
        "Novatech Solutions Pte Ltd",
        "Wrongful dismissal",
        "Closed",
        "Dismissed during probation. Claim withdrawn after settlement of one month's salary.",
    ),
    (
        "CUST-0005",
        "CASE-2026-02288",
        "Sunrise Marine Engineering",
        "Salary deduction",
        "Open",
        "Employer deducted SGD 300/month for accommodation beyond the agreed amount.",
    ),
]

SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS demo;
DROP VIEW IF EXISTS public.customer_history_view;
DROP TABLE IF EXISTS demo.customer_cases;
DROP TABLE IF EXISTS demo.customers;

CREATE TABLE demo.customers (
  customer_id TEXT PRIMARY KEY,
  customer_name TEXT NOT NULL,
  contact_number TEXT UNIQUE,
  email TEXT UNIQUE
);

CREATE TABLE demo.customer_cases (
  case_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES demo.customers(customer_id),
  company TEXT,
  case_type TEXT,
  case_status TEXT,
  case_summary TEXT
);

CREATE VIEW public.customer_history_view AS
SELECT c.customer_name, c.contact_number, c.email, k.case_id, k.company,
       k.case_type, k.case_status, k.case_summary
FROM demo.customer_cases k
JOIN demo.customers c USING (customer_id)
ORDER BY k.case_id DESC;
"""


def main() -> None:
    PGDATA.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(PGDATA, cleanup_mode=None)
    uri = server.get_uri()

    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(SCHEMA_SQL)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demo.customers (customer_id, customer_name, contact_number, email) "
                "VALUES (%s, %s, %s, %s)",
                CUSTOMERS,
            )
            cur.executemany(
                "INSERT INTO demo.customer_cases "
                "(customer_id, case_id, company, case_type, case_status, case_summary) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                CASES,
            )
        count = conn.execute(
            "SELECT COUNT(*) FROM public.customer_history_view"
        ).fetchone()[0]

    print(f"Seeded {len(CUSTOMERS)} customers, {count} cases.")
    print(f"CUSTOMER_HISTORY_DATABASE_URL={uri}")


if __name__ == "__main__":
    main()
