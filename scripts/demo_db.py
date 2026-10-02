"""Start a local embedded Postgres for demos and seed dummy customer history.

Usage: realtime-venv/bin/python scripts/demo_db.py
Idempotent: safe to re-run; it restarts the server if needed and reseeds the data.
Prints the CUSTOMER_HISTORY_DATABASE_URL to put in .env.
"""

from pathlib import Path

import pgserver
import psycopg

PGDATA = Path(__file__).resolve().parent.parent / "data" / "demo_pg"

CUSTOMERS = [
    ("Katherine Liao", "S1234567A", "12 Tampines Street 45, #08-112, Singapore 520012"),
    ("Rajesh Kumar", "G5512873K", "Blk 305 Jurong East Ave 1, #04-21, Singapore 600305"),
    ("Maria Santos", "F7734219N", "88 Serangoon Road, #10-03, Singapore 218000"),
    ("David Tan", "S7612094B", "21 Bishan Street 13, #12-45, Singapore 570021"),
    ("Nguyen Van An", "G6690312P", "Blk 110 Woodlands Drive 16, #02-88, Singapore 730110"),
]

# (nric, case_id, company, case_type, case_status, case_summary)
CASES = [
    ("S1234567A", "CASE-2025-10421", "Brightpath Logistics Pte Ltd", "Salary dispute", "Resolved",
     "Unpaid overtime for March to May 2025. Employer paid SGD 1,840 after mediation."),
    ("S1234567A", "CASE-2026-03117", "Brightpath Logistics Pte Ltd", "Leave entitlement", "Open",
     "Claims annual leave was forfeited without notice. Awaiting employer response."),
    ("G5512873K", "CASE-2025-08833", "Harbourline Construction Pte Ltd", "Workplace injury", "Closed",
     "Hand injury on site in Aug 2025. WICA claim approved and compensation paid."),
    ("G5512873K", "CASE-2026-01954", "Harbourline Construction Pte Ltd", "Work permit renewal", "Pending documents",
     "Renewal submitted Jan 2026. Medical check-up report still outstanding."),
    ("G5512873K", "CASE-2026-04402", "Harbourline Construction Pte Ltd", "Housing complaint", "Under review",
     "Reported overcrowded dormitory conditions. Inspection scheduled."),
    ("F7734219N", "CASE-2025-11760", "Evergreen Home Services", "Employment transfer", "Approved",
     "Transfer to new employer approved in Nov 2025 with previous employer's consent."),
    ("S7612094B", "CASE-2024-06215", "Novatech Solutions Pte Ltd", "Wrongful dismissal", "Closed",
     "Dismissed during probation. Claim withdrawn after settlement of one month's salary."),
    ("G6690312P", "CASE-2026-02288", "Sunrise Marine Engineering", "Salary deduction", "Open",
     "Employer deducted SGD 300/month for accommodation beyond the agreed amount."),
]

SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS demo;
DROP VIEW IF EXISTS public.customer_history_view;
DROP TABLE IF EXISTS demo.customer_cases;
DROP TABLE IF EXISTS demo.customers;

CREATE TABLE demo.customers (
  nric_worker_permit_id TEXT PRIMARY KEY,
  customer_name TEXT NOT NULL,
  address TEXT
);

CREATE TABLE demo.customer_cases (
  case_id TEXT PRIMARY KEY,
  nric_worker_permit_id TEXT NOT NULL REFERENCES demo.customers(nric_worker_permit_id),
  company TEXT,
  case_type TEXT,
  case_status TEXT,
  case_summary TEXT
);

CREATE VIEW public.customer_history_view AS
SELECT c.customer_name, c.nric_worker_permit_id, c.address, k.case_id, k.company,
       k.case_type, k.case_status, k.case_summary
FROM demo.customer_cases k
JOIN demo.customers c USING (nric_worker_permit_id)
ORDER BY k.case_id DESC;
"""


def main() -> None:
    PGDATA.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(PGDATA, cleanup_mode=None)
    uri = server.get_uri()

    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(SCHEMA_SQL)
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO demo.customers VALUES (%s, %s, %s)",
                            [(nric, name, addr) for name, nric, addr in CUSTOMERS])
            cur.executemany("INSERT INTO demo.customer_cases "
                            "(nric_worker_permit_id, case_id, company, case_type, case_status, case_summary) "
                            "VALUES (%s, %s, %s, %s, %s, %s)", CASES)
        count = conn.execute("SELECT COUNT(*) FROM public.customer_history_view").fetchone()[0]

    print(f"Seeded {len(CUSTOMERS)} customers, {count} cases.")
    print(f"CUSTOMER_HISTORY_DATABASE_URL={uri}")
    print("CUSTOMER_HISTORY_EXTRA_COLUMNS=address")


if __name__ == "__main__":
    main()
