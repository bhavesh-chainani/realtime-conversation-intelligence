"""Start a local embedded Postgres for demos and seed dummy customers, cases and issue guides.

Usage: .venv/bin/python scripts/demo_db.py
Idempotent: safe to re-run; it restarts the server if needed and reseeds everything, which also
removes cases saved from calls (POST /cases), so every run starts the demo afresh.
Prints the settings to put in .env.
"""

from datetime import date, timedelta
from pathlib import Path

import pgserver
import psycopg

PGDATA = Path(__file__).resolve().parent.parent / "data" / "demo_pg"

# (customer_id, name, contact_number, email). Phones are stored in mixed formats on purpose: the lookup
# compares the last 8 digits, as a real case system will not be consistent either.
CUSTOMERS = [
    ("CUST-0001", "Katherine Liao", "+65 9123 4567", "katherine.liao@gmail.com"),
    ("CUST-0002", "Rajesh Kumar", "8234 5678", "rajesh.kumar@gmail.com"),
    ("CUST-0003", "Maria Santos", "+6593456789", "maria.santos@gmail.com"),
    ("CUST-0004", "David Tan", "9456-7890", "david.tan@gmail.com"),
    ("CUST-0005", "Nguyen Van An", "8567 8901", "nguyen.vanan@gmail.com"),
    ("CUST-0006", "Siti Aminah binte Ahmad", "9678 1234", "siti.aminah@gmail.com"),
    ("CUST-0007", "Arjun Pillai", "+65 8789 0123", "arjun.pillai@gmail.com"),
    ("CUST-0008", "Chen Li Mei", "9890-1234", "limei.chen@gmail.com"),
    ("CUST-0009", "Mohammad Rizal", "8901 2345", "rizal.mohammad@gmail.com"),
    ("CUST-0010", "Grace Lim", "6123 4567", "grace.lim@gmail.com"),
]

# Dates are days relative to the day the script runs, so open cases always have a current or overdue
# follow-up. (customer_id, case_id, company, case_type, case_status, case_summary,
#  opened days ago, next_action, follow-up due in N days (negative = overdue) or None)
CASES = [
    # Katherine: a resolved claim and an open leave case with the same employer (repeat-employer story).
    ("CUST-0001", "CASE-2025-10421", "Brightpath Logistics Pte Ltd", "Salary dispute", "Resolved",
     "Unpaid overtime for March to May 2025. Employer paid SGD 1,840 after TADM mediation.",
     400, None, None),
    ("CUST-0001", "CASE-2026-03117", "Brightpath Logistics Pte Ltd", "Leave entitlement", "Open",
     "Six days of annual leave forfeited without notice. Complaint raised with HR; awaiting the "
     "employer's written reply.",
     40, "Centre to chase Brightpath HR for a written reply", 3),
    # Rajesh: an overdue document on his permit renewal (follow-up story) and a housing complaint.
    ("CUST-0002", "CASE-2025-08833", "Harbourline Construction Pte Ltd", "Work injury", "Closed",
     "Hand injury on site in Aug 2025. WICA claim approved and compensation paid.",
     400, None, None),
    ("CUST-0002", "CASE-2026-01954", "Harbourline Construction Pte Ltd", "Work permit renewal",
     "Pending documents",
     "Renewal submitted by the employer. Medical check-up report still outstanding.",
     60, "Caller to send the medical check-up report", -2),
    ("CUST-0002", "CASE-2026-04402", "Harbourline Construction Pte Ltd", "Housing complaint", "Under review",
     "Reported an overcrowded dormitory (14 workers per room). MOM inspection requested.",
     25, "Centre to share the MOM inspection outcome with the caller", 10),
    ("CUST-0003", "CASE-2025-11760", "Evergreen Home Services", "Employment transfer", "Approved",
     "Transfer to a new employer approved in Nov 2025 with the previous employer's consent.",
     300, None, None),
    ("CUST-0004", "CASE-2024-06215", "Novatech Solutions Pte Ltd", "Wrongful dismissal", "Closed",
     "Dismissed during probation. Claim withdrawn after settlement of one month's salary.",
     700, None, None),
    ("CUST-0004", "CASE-2026-04805", "BlueOcean Fintech Pte Ltd", "Late salary", "Open",
     "Salary paid two to three weeks late for the last three months.",
     7, "Caller to send bank statements showing the pay dates", 2),
    ("CUST-0005", "CASE-2026-02288", "Sunrise Marine Engineering", "Salary deduction", "Mediation scheduled",
     "Employer deducts SGD 300/month for accommodation against the agreed SGD 150. TADM claim filed.",
     30, "Caller to attend TADM mediation with payslips", 6),
    ("CUST-0006", "CASE-2025-07712", "Orchid Retail Pte Ltd", "Unpaid salary", "Resolved",
     "Final month's salary (SGD 2,100) unpaid after resignation. Paid in full after TADM mediation.",
     250, None, None),
    ("CUST-0007", "CASE-2026-00931", "Kestrel Tech Pte Ltd", "Wrongful dismissal", "Closed",
     "Dismissed after raising safety concerns. Settled at TADM mediation for two months' salary.",
     220, None, None),
    # Li Mei: a recent work injury with documents overdue (WICA story).
    ("CUST-0008", "CASE-2026-04718", "Golden Wok Restaurant", "Work injury", "Open",
     "Burn injury in the kitchen while on duty. Employer reported it to MOM; WICA claim in progress.",
     14, "Caller to send all MCs and the hospital report", -1),
    ("CUST-0008", "CASE-2026-04719", "Golden Wok Restaurant", "Medical leave pay", "Open",
     "Employer has not paid wages for three weeks of medical leave after the injury.",
     10, "Centre to confirm medical leave wages with the employer", 4),
    ("CUST-0009", "CASE-2026-03950", "Metro Facilities Services", "Unauthorised deductions",
     "Mediation scheduled",
     "SGD 450 deducted for a damaged floor scrubber without written consent.",
     35, "Caller to attend TADM mediation", 9),
    ("CUST-0010", "CASE-2025-09102", "Sunshine Childcare Centre", "Working hours", "Closed",
     "Advised on rest days and overtime pay; the employer revised the roster.",
     330, None, None),
    ("CUST-0010", "CASE-2026-04120", "Sunshine Childcare Centre", "Overtime pay", "Advice given",
     "Overtime for weekend events not paid since July. Advised to file a TADM claim.",
     21, "Caller to confirm the TADM claim was filed", -3),
]  # fmt: skip

# What the centre advises per issue type. Demo content: check with the centre's legal team before use.
# (issue_type, applies_when, facts_to_gather, documents, route, deadline, follow_up)
ISSUE_GUIDES = [
    ("Unpaid or late salary",
     "Salary, overtime pay or final pay not paid, or paid late.",
     "Employer; which months or amounts are unpaid; whether the caller still works there (last day if "
     "not); whether they have asked the employer in writing.",
     "Payslips, bank statements showing pay dates, contract or offer letter, timesheets, messages with "
     "the employer.",
     "File a salary claim online with TADM (Tripartite Alliance for Dispute Management), which arranges "
     "mediation. If mediation fails, the claim can go to the Employment Claims Tribunals (ECT).",
     "Generally within 1 year of the amount falling due if still employed, or within 6 months of leaving.",
     "Call back in 7 days to confirm the TADM claim was submitted and help with missing documents."),
    ("Unauthorised salary deductions",
     "Money deducted from pay for damage, losses, accommodation, fines or other charges.",
     "Amount and reason for each deduction; whether the caller agreed in writing; which months; whether "
     "still employed.",
     "Payslips showing the deductions, contract, any signed consent, messages about the deductions.",
     "Deductions generally need a lawful basis or the employee's written consent. Unauthorised deductions "
     "can be claimed back through a TADM salary claim; mediation comes first.",
     "Generally within 1 year if still employed, or within 6 months of leaving.",
     "Call back in 7 days to confirm the claim was filed."),
    ("Working hours, rest days and overtime",
     "Long hours, back-to-back shifts, no rest day, or overtime not paid.",
     "Typical hours per day and week; rest days per week; whether overtime is paid; monthly basic salary "
     "(the hours rules generally cover workmen earning up to SGD 4,500 and other employees up to "
     "SGD 2,600); rosters or clock-in records.",
     "Rosters, clock-in records, payslips, contract, messages about scheduling.",
     "For covered employees, work is generally capped at 12 hours a day and overtime at 72 hours a month, "
     "with one rest day a week. Unpaid overtime can be claimed through TADM; ongoing breaches of hours or "
     "rest days can be reported to MOM.",
     "Overtime claims: generally within 1 year if still employed, or 6 months after leaving. Reports to "
     "MOM can be made at any time.",
     "Call back in 14 days to check whether the roster changed or a claim or report was made."),
    ("Annual leave and leave pay",
     "Annual leave refused or forfeited, leave not paid out on leaving, or medical leave wages unpaid.",
     "Length of service; leave entitlement in the contract; days forfeited or unpaid; whether still "
     "employed.",
     "Contract or handbook leave policy, leave records, MCs, payslips, messages with HR.",
     "Leave and leave-pay disputes are salary-related claims: TADM mediation first, then the ECT if "
     "unresolved.",
     "Generally within 1 year if still employed, or within 6 months of leaving.",
     "Call back in 7 days, after the employer's reply is due."),
    ("Wrongful dismissal",
     "Dismissed without just cause, for a discriminatory reason, or to deprive the caller of benefits.",
     "Date of dismissal and last day of work; reason given; whether notice or notice pay was given; "
     "length of service; whether the caller resigned or was dismissed.",
     "Dismissal letter or messages, contract, payslips, warning letters, notes of what was said.",
     "File a wrongful dismissal claim online with TADM for mediation; unresolved claims go to the ECT.",
     "Generally within 1 month of the last day of employment, so act quickly.",
     "Call back in 3 days: the deadline is short."),
    ("Work injury",
     "Injured at work, or ill because of work.",
     "Date and how it happened; whether it was reported to the employer; days of medical leave; whether "
     "medical bills and medical leave wages are being paid.",
     "MCs, medical reports, hospital bills and receipts, photos, names of witnesses.",
     "Compensation is claimed under the Work Injury Compensation Act (WICA) with MOM, usually without "
     "going to court. Employers generally must report accidents with more than 3 days of MC to MOM.",
     "Generally within 1 year of the accident.",
     "Call back in 7 days to confirm the employer reported it and the documents were sent."),
    ("Work pass issues",
     "Work permit or pass renewal, cancellation or transfer, or the employer holding the caller's documents.",
     "Pass type and expiry date; what the employer has applied for; outstanding documents (e.g. medical "
     "check-up); whether the employer holds the passport or pass card.",
     "Pass card or IPA letter, passport details, medical check-up report, messages with the employer or "
     "agent.",
     "The employer applies to MOM for renewals and transfers; the caller can check pass status in the "
     "SGWorkPass app. Employers should not keep a worker's passport or pass card; this can be reported to "
     "MOM.",
     "Before the pass expires; renewals are usually done well in advance.",
     "Call back a week before the pass expires to confirm the renewal went through."),
    ("Unfair treatment, retaliation or housing",
     "Discrimination, harassment, retaliation for a complaint, or poor dormitory conditions.",
     "What happened and when; who was involved; whether it followed a complaint or claim; witnesses or "
     "written evidence; for housing, the dormitory and conditions.",
     "Messages, emails, photos, names of witnesses, dates of incidents.",
     "Workplace fairness and discrimination concerns can be raised with TAFEP (Tripartite Alliance for "
     "Fair and Progressive Employment Practices). Poor dormitory conditions can be reported to MOM. "
     "Retaliation after an earlier complaint should be added to that case.",
     "As soon as possible, while evidence is fresh.",
     "Call back in 7 days to check for any response."),
]  # fmt: skip

SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS demo;
DROP VIEW IF EXISTS public.customer_history_view;
DROP VIEW IF EXISTS public.issue_guide_view;
DROP TABLE IF EXISTS demo.customer_cases;
DROP TABLE IF EXISTS demo.customers;
DROP TABLE IF EXISTS demo.issue_guides;

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
  case_summary TEXT,
  opened_on DATE,
  next_action TEXT,
  follow_up_due DATE
);

CREATE TABLE demo.issue_guides (
  issue_type TEXT PRIMARY KEY,
  applies_when TEXT,
  facts_to_gather TEXT,
  documents TEXT,
  route TEXT,
  deadline TEXT,
  follow_up TEXT
);

CREATE VIEW public.customer_history_view AS
SELECT c.customer_name, c.contact_number, c.email, k.case_id, k.company,
       k.case_type, k.case_status, k.case_summary, k.opened_on, k.next_action, k.follow_up_due
FROM demo.customer_cases k
JOIN demo.customers c USING (customer_id)
ORDER BY k.case_id DESC;

CREATE VIEW public.issue_guide_view AS SELECT * FROM demo.issue_guides;
"""


def main() -> None:
    PGDATA.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(PGDATA, cleanup_mode=None)
    uri = server.get_uri()
    today = date.today()

    def days(n: int | None) -> date | None:
        return None if n is None else today + timedelta(days=n)

    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(SCHEMA_SQL)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demo.customers (customer_id, customer_name, contact_number, email) "
                "VALUES (%s, %s, %s, %s)",
                CUSTOMERS,
            )
            cur.executemany(
                "INSERT INTO demo.customer_cases (customer_id, case_id, company, case_type, case_status, "
                "case_summary, opened_on, next_action, follow_up_due) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(*c[:6], days(-c[6]), c[7], days(c[8])) for c in CASES],
            )
            cur.executemany(
                "INSERT INTO demo.issue_guides VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ISSUE_GUIDES,
            )
        count = conn.execute("SELECT COUNT(*) FROM public.customer_history_view").fetchone()[0]

    print(f"Seeded {len(CUSTOMERS)} customers, {count} cases, {len(ISSUE_GUIDES)} issue guides.")
    print(f"CUSTOMER_HISTORY_DATABASE_URL={uri}")
    print("CASE_STORE_ENABLED=true")


if __name__ == "__main__":
    main()
