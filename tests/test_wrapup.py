"""Wrap-up agent validation, the /wrapup and /cases endpoints, and the case store helpers."""

from __future__ import annotations

from datetime import date

from backend import config as cfg
from backend.agents.wrapup_agent import validate_wrapup
from backend.case_store import format_phone, next_id

TODAY = date(2026, 10, 5)
CASES = [
    {"case_id": "CASE-2026-01954", "status": "Pending documents"},
    {"case_id": "CASE-2025-08833", "status": "Closed"},
]
VERIFIED = {
    "name": "Rajesh Kumar",
    "contact_number": "82345678",
    "record_match": "contact_number",
}


def _raw(**overrides):
    raw = {
        "summary": "  Medical report   sent. ",
        "issue_type": "Work pass issues",
        "case": {
            "action": "update",
            "case_id": "CASE-2026-01954",
            "status": "Pending documents",
        },
        "actions": [
            {"owner": "caller", "action": "Send the report", "due": "2026-10-08"},
            {"owner": "boss", "action": "Chase employer", "due": "next week"},
            {"owner": "caller", "action": ""},
        ],
        "next_action": "",
        "follow_up": {
            "date": "2026-10-09",
            "channel": "phone",
            "reason": "Check the renewal.",
        },
        "message_to_caller": {
            "channel": "sms",
            "subject": "Hi",
            "body": "Hi Rajesh,\n- MC",
        },
    }
    raw.update(overrides)
    return raw


def test_validate_wrapup_normalises_fields():
    w = validate_wrapup(_raw(), VERIFIED, CASES, TODAY)
    assert w["summary"] == "Medical report sent."
    assert w["case"] == {
        "action": "update",
        "case_id": "CASE-2026-01954",
        "case_type": "Work pass issues",
        "company": "",
        "status": "Pending documents",
    }
    # Unknown owners become the centre, bad dates are dropped, empty actions skipped.
    assert w["actions"] == [
        {"owner": "caller", "action": "Send the report", "due": "2026-10-08"},
        {"owner": "centre", "action": "Chase employer", "due": ""},
    ]
    assert w["next_action"] == "Send the report"
    assert w["follow_up"] == {
        "date": "2026-10-09",
        "channel": "phone",
        "reason": "Check the renewal.",
    }
    # No email on the card: the message goes by SMS, without a subject.
    assert w["message_to_caller"] == {
        "channel": "sms",
        "subject": "",
        "body": "Hi Rajesh,\n- MC",
    }


def test_update_needs_an_open_verified_case():
    closed = _raw(case={"action": "update", "case_id": "CASE-2025-08833", "status": "Resolved"})
    assert validate_wrapup(closed, VERIFIED, CASES, TODAY)["case"]["action"] == "new"
    invented = _raw(case={"action": "update", "case_id": "CASE-9999-00001"})
    assert validate_wrapup(invented, VERIFIED, CASES, TODAY)["case"]["case_id"] is None


def test_follow_up_defaults_and_bad_values():
    w = validate_wrapup(
        _raw(follow_up={"date": "2026-01-01", "channel": "fax"}, case={"status": "Weird"}),
        {"email": "a@b.com"},
        [],
        TODAY,
    )
    assert w["follow_up"]["date"] == "2026-10-12"  # past date: a week from today
    assert w["follow_up"]["channel"] == "email"  # no phone on the card
    assert w["case"]["status"] == "Open"
    assert w["message_to_caller"]["channel"] == "email"
    assert validate_wrapup("nonsense", {}, None, TODAY)["issue_type"] == "Other"


def test_case_store_helpers():
    assert next_id(["CASE-2025-10421", "CASE-2026-04805"], "CASE-2026-", 5) == "CASE-2026-10422"
    assert next_id([], "CUST-", 4) == "CUST-0001"
    assert next_id(["CUST-0010", "odd"], "CUST-", 4) == "CUST-0011"
    assert format_phone("81112222") == "8111 2222"


def test_wrapup_endpoint(client, monkeypatch):
    seen = {}

    async def fake_generate(transcript, profile, cases):
        seen.update(transcript=transcript, profile=profile, cases=cases)
        return {"summary": "ok"}

    monkeypatch.setattr("backend.agents.wrapup_agent.generate_wrapup", fake_generate)
    body = client.post(
        "/wrapup",
        json={
            "turns": [
                {"role": "staff", "text": "How can I help?"},
                {"role": "customer", "text": "My salary has not been paid."},
            ],
            "customer": {"name": "Ahmad Rahim", "address": "x"},
            "history": {
                "match_strategy": "contact_number",
                "cases": [{"case_id": "C1"}],
            },
        },
    ).json()
    assert body == {"status": "ok", "wrapup": {"summary": "ok"}}
    assert seen["transcript"].endswith("Customer: My salary has not been paid.")
    assert seen["profile"] == {"name": "Ahmad Rahim", "record_match": "contact_number"}
    assert seen["cases"][0]["case_id"] == "C1"


def test_wrapup_endpoint_reports_failures(client, monkeypatch):
    async def boom(*args):
        raise RuntimeError("llm down")

    monkeypatch.setattr("backend.agents.wrapup_agent.generate_wrapup", boom)
    turns = [{"role": "customer", "text": "My salary has not been paid for two months."}]
    assert client.post("/wrapup", json={"turns": turns}).json()["status"] == "error"
    assert (
        client.post("/wrapup", json={"turns": [{"role": "customer", "text": "hi"}]}).json()["status"]
        == "error"
    )


def test_save_case_endpoint(client, monkeypatch):
    payload = {"customer": {"contact_number": "8111 2222"}, "wrapup": {"summary": "x"}}

    monkeypatch.setattr(cfg, "CASE_STORE_ENABLED", False)
    assert client.post("/cases", json=payload).json()["status"] == "disabled"

    monkeypatch.setattr(cfg, "CASE_STORE_ENABLED", True)
    saved = {
        "case_id": "CASE-2026-10422",
        "customer_id": "CUST-0011",
        "action": "created",
    }
    monkeypatch.setattr("backend.case_store.save", lambda customer, wrapup: saved)
    assert client.post("/cases", json=payload).json() == {"status": "saved", **saved}

    def invalid(customer, wrapup):
        raise ValueError("A contact number or email is needed to save the case.")

    monkeypatch.setattr("backend.case_store.save", invalid)
    assert client.post("/cases", json=payload).json()["status"] == "invalid"
