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
                "success": True,
                "found": True,
                "match_strategy": "contact_number",
                "customer": {
                    "name": "Raja",
                    "contact_number": "8234 5678",
                    "email": "raja@example.com",
                },
                "history_summary": "Raja has 1 matching case in customer history.",
                "message": "Found 1 customer history entry.",
                "cases": [
                    {
                        "case_id": "#CH298D",
                        "company": "ABC",
                        "type": "Employer dispute",
                        "status": "Resolved",
                        "summary": "Salary underpayment complaint settled through mediation.",
                    }
                ],
            }
        if name == "":
            return {
                "status": "invalid_input",
                "success": False,
                "found": False,
                "customer": {"name": None, "contact_number": None, "email": None},
                "history_summary": "",
                "message": "Enter a customer name, contact number or email to search history.",
                "cases": [],
            }
        return {
            "status": "not_found",
            "success": True,
            "found": False,
            "match_strategy": "name",
            "customer": {"name": name, "contact_number": None, "email": None},
            "history_summary": "No prior cases found for the provided details.",
            "message": "No prior cases found for the provided details.",
            "cases": [],
        }


def test_customer_history_endpoint_happy_path(client, monkeypatch):
    monkeypatch.setattr(
        "backend.customer_history.customer_history_service", _FakeService()
    )

    r = client.post(
        "/customer-history",
        json={"contact_number": "+65 8234 5678"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["success"] is True
    assert body["found"] is True
    assert body["customer"]["name"] == "Raja"
    assert len(body["cases"]) == 1
    # Keyed on the record's phone and email, which the caller card now holds.
    assert body["lookup_key"] == "id:82345678|raja@example.com"
    # Only a phone / email match prefills the caller card.
    assert body["prefill"] == {
        "name": "Raja",
        "contact_number": "8234 5678",
        "email": "raja@example.com",
    }


def test_customer_history_endpoint_no_match(client, monkeypatch):
    monkeypatch.setattr(
        "backend.customer_history.customer_history_service", _FakeService()
    )

    r = client.post("/customer-history", json={"name": "Unknown Person"})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "not_found"
    assert body["success"] is True
    assert body["found"] is False
    assert body["cases"] == []
    assert body["lookup_key"] == "name:unknown person"
    assert body["prefill"] is None


def test_customer_history_service_requires_lookup_fields():
    body = customer_history_service.lookup("", "", "")

    assert body["status"] == "invalid_input"
    assert body["success"] is False
    assert body["found"] is False
    assert body["cases"] == []


def test_lookup_returns_extra_columns_open_count_and_open_cases_first(monkeypatch):
    from backend import config as cfg

    rows = [
        {
            "customer_name": "Katherine Liao",
            "contact_number": "+65 9123 4567",
            "email": "katherine.liao@example.com",
            "region": "East",
            "case_id": "CASE-2025-10421",
            "company": "Brightpath Logistics Pte Ltd",
            "case_type": "Salary dispute",
            "case_status": "Resolved",
            "case_summary": "Paid after mediation.",
        },
        {
            "customer_name": "Katherine Liao",
            "contact_number": "+65 9123 4567",
            "email": "katherine.liao@example.com",
            "region": "East",
            "case_id": "CASE-2026-03117",
            "company": "Brightpath Logistics Pte Ltd",
            "case_type": "Leave entitlement",
            "case_status": "Open",
            "case_summary": "Awaiting employer response.",
        },
    ]
    monkeypatch.setattr(cfg, "CUSTOMER_HISTORY_DATABASE_URL", "postgresql://fake")
    monkeypatch.setattr(
        cfg, "CUSTOMER_HISTORY_EXTRA_COLUMNS", ["region", "bad column;"]
    )
    queried = []

    def fake_query(clean_phone, clean_email, clean_name):
        queried.append((clean_phone, clean_email, clean_name))
        return rows, "contact_number"

    monkeypatch.setattr(customer_history_service, "_query_rows", fake_query)

    body = customer_history_service.lookup(
        None, "+65 9123 4567", " K.Liao@Example.com "
    )

    # The service queries with normalised values.
    assert queried == [("91234567", "k.liao@example.com", "")]
    assert body["status"] == "ok"
    assert body["customer"] == {
        "name": "Katherine Liao",
        "contact_number": "+65 9123 4567",
        "email": "katherine.liao@example.com",
        "region": "East",
    }
    assert "bad column;" not in body["customer"]
    assert body["open_count"] == 1
    assert body["companies"] == ["Brightpath Logistics Pte Ltd"]
    assert [c["case_id"] for c in body["cases"]] == [
        "CASE-2026-03117",
        "CASE-2025-10421",
    ]


def test_customer_record_names_how_it_was_matched():
    cases = [{"case_id": "CASE-1", "status": "Open"}]
    profile = {
        "name": "Katherine Liao",
        "contact_number": "91234567",
        "email": "k@example.com",
    }

    by_phone = format_customer_record(
        {**profile, "record_match": "contact_number"}, cases
    )
    assert "matched on contact number" in by_phone
    assert "Name: Katherine Liao | Phone: 91234567 | Email: k@example.com" in by_phone

    by_email = format_customer_record({**profile, "record_match": "email"}, cases)
    assert "matched on email" in by_email

    by_name = format_customer_record(
        {"name": "Katherine Liao", "record_match": "name"}, cases
    )
    assert "NAME ONLY" in by_name and "ask for the caller's contact number" in by_name


def test_name_only_match_does_not_reveal_contact_details(monkeypatch):
    from backend import config as cfg

    row = {
        "customer_name": "David Tan",
        "contact_number": "9456-7890",
        "email": "david.tan@example.com",
        "case_id": "CASE-1",
        "case_status": "Closed",
    }
    monkeypatch.setattr(cfg, "CUSTOMER_HISTORY_DATABASE_URL", "postgresql://fake")
    monkeypatch.setattr(
        customer_history_service, "_query_rows", lambda *a: ([row], "name")
    )

    body = customer_history_service.lookup("david tan", None, None)

    assert body["status"] == "ok" and len(body["cases"]) == 1
    assert body["customer"] == {
        "name": "David Tan",
        "contact_number": None,
        "email": None,
    }


def test_caller_card_lists_what_is_known_and_what_the_check_needs():
    card = format_caller_card({})
    assert "Name: not given | Contact number: not given" in card
    assert "needs the caller's full name and contact number" in card

    card = format_caller_card({"name": "Katherine", "lookup_status": "not_started"})
    assert "Name: Katherine" in card and "needs the caller's contact number" in card

    card = format_caller_card(
        {"contact_number": "9123 45", "lookup_status": "not_started"}
    )
    assert "complete contact number" in card


def test_caller_card_history_check_states():
    def check(**profile):
        return format_caller_card(profile).split("HISTORY CHECK: ", 1)[1]

    assert check(lookup_status="pending").startswith("in progress")
    assert "verified (matched on email)" in check(
        lookup_status="verified", record_match="email"
    )
    assert "NAME ONLY" in check(lookup_status="name", name="Katherine Liao")
    # Not found by phone: a new caller. Not found by name only: ask for a contact number.
    assert "new caller" in check(lookup_status="not_found", contact_number="81112222")
    assert "Ask for the caller's contact number" in check(
        lookup_status="not_found", name="Katherine Liao"
    )
    assert "could not be checked" in check(lookup_status="unavailable")
