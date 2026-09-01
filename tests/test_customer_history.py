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
