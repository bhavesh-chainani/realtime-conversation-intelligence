from __future__ import annotations

from backend.customer_history import (
    customer_history_service,
    format_caller_card,
    format_customer_record,
)


class _FakeService:
    def lookup(self, name, phone, email):
        if phone == "+65 8234 5678":
            return {
                "status": "ok",
                "match_strategy": "contact_number",
                "customer": {"name": "Raja", "contact_number": "8234 5678", "email": "raja@gmail.com"},
                "cases": [
                    {
                        "case_id": "#CH298D",
                        "company": "ABC",
                        "type": "Employer dispute",
                        "status": "Resolved",
                        "summary": "Salary underpayment complaint settled through mediation.",
                    }
                ],
                "open_count": 0,
                "summary": "Raja has 1 matching case in customer history.",
            }
        return {
            "status": "not_found",
            "summary": "No prior cases found for the provided details.",
            "cases": [],
        }


def test_customer_history_endpoint_happy_path(client, monkeypatch):
    monkeypatch.setattr("backend.customer_history.customer_history_service", _FakeService())

    r = client.post(
        "/customer-history",
        json={"contact_number": "+65 8234 5678"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["customer"]["name"] == "Raja"
    assert len(body["cases"]) == 1
    # Keyed on the record's phone and email, which the caller card now holds.
    assert body["lookup_key"] == "id:82345678|raja@gmail.com"
    # Only a phone / email match prefills the caller card.
    assert body["prefill"] == {
        "name": "Raja",
        "contact_number": "8234 5678",
        "email": "raja@gmail.com",
    }


def test_customer_history_endpoint_no_match(client, monkeypatch):
    monkeypatch.setattr("backend.customer_history.customer_history_service", _FakeService())

    r = client.post("/customer-history", json={"name": "Unknown Person"})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "not_found"
    assert body["cases"] == []
    assert body["lookup_key"] == "name:unknown person"
    assert body["prefill"] is None


def test_customer_history_service_requires_lookup_fields():
    body = customer_history_service.lookup("", "", "")

    assert body["status"] == "invalid_input"
    assert body["cases"] == []


def test_lookup_returns_the_record_open_count_and_open_cases_first(monkeypatch):
    from backend import config as cfg

    rows = [
        {
            "customer_name": "Katherine Liao",
            "contact_number": "+65 9123 4567",
            "email": "katherine.liao@gmail.com",
            "case_id": "CASE-2025-10421",
            "company": "Brightpath Logistics Pte Ltd",
            "case_type": "Salary dispute",
            "case_status": "Resolved",
            "case_summary": "Paid after mediation.",
        },
        {
            "customer_name": "Katherine Liao",
            "contact_number": "+65 9123 4567",
            "email": "katherine.liao@gmail.com",
            "case_id": "CASE-2026-03117",
            "company": "Brightpath Logistics Pte Ltd",
            "case_type": "Leave entitlement",
            "case_status": "Open",
            "case_summary": "Awaiting employer response.",
            "follow_up_due": "2026-10-08",
        },
    ]
    monkeypatch.setattr(cfg, "CUSTOMER_HISTORY_DATABASE_URL", "postgresql://fake")
    queried = []

    def fake_query(clean_phone, clean_email, clean_name):
        queried.append((clean_phone, clean_email, clean_name))
        return rows, "contact_number"

    monkeypatch.setattr(customer_history_service, "_query_rows", fake_query)

    body = customer_history_service.lookup(None, "+65 9123 4567", " K.Liao@Gmail.com ")

    # The service queries with normalised values.
    assert queried == [("91234567", "k.liao@gmail.com", "")]
    assert body["status"] == "ok"
    assert body["customer"] == {
        "name": "Katherine Liao",
        "contact_number": "+65 9123 4567",
        "email": "katherine.liao@gmail.com",
    }
    assert body["open_count"] == 1
    assert (
        body["summary"]
        == "Katherine Liao has 2 matching cases in customer history. Matched on contact number."
    )
    assert [c["case_id"] for c in body["cases"]] == ["CASE-2026-03117", "CASE-2025-10421"]
    # View columns are mapped to case fields; missing ones become "".
    assert (
        body["cases"][0]["type"] == "Leave entitlement" and body["cases"][0]["follow_up_due"] == "2026-10-08"
    )
    assert body["cases"][1]["next_action"] == ""


def test_a_failed_lookup_does_not_leak_the_database_error(monkeypatch):
    from backend import config as cfg

    monkeypatch.setattr(cfg, "CUSTOMER_HISTORY_DATABASE_URL", "postgresql://fake")

    def boom(*args):
        raise RuntimeError("password authentication failed for user demo")

    monkeypatch.setattr(customer_history_service, "_query_rows", boom)
    body = customer_history_service.lookup(None, "91234567", None)
    assert body["status"] == "error" and "password" not in str(body)


def test_customer_record_names_how_it_was_matched():
    cases = [{"case_id": "CASE-1", "status": "Open"}]
    profile = {
        "name": "Katherine Liao",
        "contact_number": "91234567",
        "email": "k@gmail.com",
    }

    by_phone = format_customer_record({**profile, "record_match": "contact_number"}, cases)
    assert "matched on contact number" in by_phone
    assert "Name: Katherine Liao | Phone: 91234567 | Email: k@gmail.com" in by_phone

    by_email = format_customer_record({**profile, "record_match": "email"}, cases)
    assert "matched on email" in by_email

    by_name = format_customer_record({"name": "Katherine Liao", "record_match": "name"}, cases)
    assert "NAME ONLY" in by_name and "ask for the caller's contact number" in by_name


def test_name_only_match_does_not_reveal_contact_details(monkeypatch):
    from backend import config as cfg

    row = {
        "customer_name": "David Tan",
        "contact_number": "9456-7890",
        "email": "david.tan@gmail.com",
        "case_id": "CASE-1",
        "case_status": "Closed",
    }
    monkeypatch.setattr(cfg, "CUSTOMER_HISTORY_DATABASE_URL", "postgresql://fake")
    monkeypatch.setattr(customer_history_service, "_query_rows", lambda *a: ([row], "name"))

    body = customer_history_service.lookup("david tan", None, None)

    assert body["status"] == "ok" and len(body["cases"]) == 1
    assert body["customer"] == {
        "name": "David Tan",
        "contact_number": None,
        "email": None,
    }


def test_caller_card_lists_what_is_known_and_what_to_ask_for():
    card = format_caller_card({})
    assert "Name: not given | Contact number: not given | Email: not given" in card
    assert "Ask for the caller's full name, contact number and email address in one question" in card

    card = format_caller_card({"name": "Katherine Liao", "lookup_status": "not_started"})
    assert "Name: Katherine Liao" in card
    assert "Ask for the caller's contact number and email address in one question" in card

    card = format_caller_card({"contact_number": "9123 45", "lookup_status": "not_started"})
    assert "a complete contact number" in card


def test_caller_card_history_check_states():
    def check(**profile):
        return format_caller_card(profile).split("HISTORY CHECK: ", 1)[1]

    # A phone was heard and is being looked up: still collect the email meanwhile.
    pending = check(lookup_status="pending", name="Rajesh Kumar", contact_number="82345678")
    assert pending.startswith("in progress") and "Ask for the caller's email address" in pending
    assert "verified (matched on email)" in check(lookup_status="verified", record_match="email")
    by_name = check(lookup_status="name", name="Katherine Liao")
    assert "NAME ONLY" in by_name and "contact number and email address to confirm" in by_name
    # Not found by phone: ask for the email to check once more; with both, a new caller.
    assert "Ask for the caller's email address" in check(
        lookup_status="not_found", name="A B", contact_number="81112222"
    )
    assert "new caller" in check(
        lookup_status="not_found",
        name="A B",
        contact_number="81112222",
        email="a@b.com",
    )
    # Not found by name only: ask for both contact details.
    assert "contact number and email address in one question, to check again" in check(
        lookup_status="not_found", name="Katherine Liao"
    )
    assert "could not be checked" in check(lookup_status="unavailable")


def test_customer_record_shows_next_action_and_overdue_follow_up():
    from datetime import date

    cases = [
        {
            "case_id": "CASE-2026-01954",
            "status": "Pending documents",
            "opened_on": "2026-08-06",
            "next_action": "Caller to send the medical report",
            "follow_up_due": "2026-10-03",
        },
        {
            "case_id": "CASE-2025-08833",
            "status": "Closed",
            "next_action": "old",
            "follow_up_due": "2025-01-01",
        },
    ]
    text = format_customer_record({"record_match": "contact_number"}, cases, date(2026, 10, 5))
    assert (
        "opened 2026-08-06 | next: Caller to send the medical report | follow-up due 2026-10-03 (OVERDUE)"
    ) in text
    assert "OVERDUE ACTIONS TO RAISE FIRST: CASE-2026-01954: Caller to send the medical report" in text
    closed_line = text.splitlines()[-1]
    assert "next:" not in closed_line and "follow-up" not in closed_line
