from __future__ import annotations

from backend.customer_history import customer_history_service


class _FakeService:
    def lookup(self, name, nric_worker_permit_id):
        if nric_worker_permit_id == "S1234567A":
            return {
                "status": "ok",
                "success": True,
                "found": True,
                "match_strategy": "nric_worker_permit_id",
                "customer": {
                    "name": "Raja",
                    "nric_worker_permit_id": "S1234567A",
                },
                "history_summary": "Raja (S1234567A) has 1 matching case in customer history.",
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
                "customer": {"name": None, "nric_worker_permit_id": None},
                "history_summary": "",
                "message": "Enter a customer name or NRIC / Work Permit ID to search history.",
                "cases": [],
            }
        return {
            "status": "not_found",
            "success": True,
            "found": False,
            "match_strategy": "name",
            "customer": {"name": name, "nric_worker_permit_id": None},
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
        json={"nric_worker_permit_id": "S1234567A"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["success"] is True
    assert body["found"] is True
    assert body["customer"]["name"] == "Raja"
    assert len(body["cases"]) == 1


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


def test_customer_history_service_requires_lookup_fields():
    body = customer_history_service.lookup("", "")

    assert body["status"] == "invalid_input"
    assert body["success"] is False
    assert body["found"] is False
    assert body["cases"] == []


def test_lookup_returns_extra_columns_open_count_and_open_cases_first(monkeypatch):
    from backend import config as cfg

    rows = [
        {
            "customer_name": "Katherine Liao",
            "nric_worker_permit_id": "S1234567A",
            "address": "12 Tampines Street 45",
            "case_id": "CASE-2025-10421",
            "company": "Brightpath Logistics Pte Ltd",
            "case_type": "Salary dispute",
            "case_status": "Resolved",
            "case_summary": "Paid after mediation.",
        },
        {
            "customer_name": "Katherine Liao",
            "nric_worker_permit_id": "S1234567A",
            "address": "12 Tampines Street 45",
            "case_id": "CASE-2026-03117",
            "company": "Brightpath Logistics Pte Ltd",
            "case_type": "Leave entitlement",
            "case_status": "Open",
            "case_summary": "Awaiting employer response.",
        },
    ]
    monkeypatch.setattr(cfg, "CUSTOMER_HISTORY_DATABASE_URL", "postgresql://fake")
    monkeypatch.setattr(cfg, "CUSTOMER_HISTORY_EXTRA_COLUMNS", ["address", "bad column;"])
    monkeypatch.setattr(
        customer_history_service,
        "_query_rows",
        lambda clean_id, clean_name: (rows, "nric_worker_permit_id"),
    )

    body = customer_history_service.lookup(None, "S1234567A")

    assert body["status"] == "ok"
    assert body["customer"]["address"] == "12 Tampines Street 45"
    assert "bad column;" not in body["customer"]
    assert body["open_count"] == 1
    assert body["companies"] == ["Brightpath Logistics Pte Ltd"]
    assert [c["case_id"] for c in body["cases"]] == ["CASE-2026-03117", "CASE-2025-10421"]
