from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from backend.call_summary import compute_call_summary

CASES = [
    {"case_id": "CASE-2026-03117", "company": "Brightpath", "type": "Leave", "status": "Open", "summary": "x"},
    {"case_id": "CASE-2025-10421", "company": "Brightpath", "type": "Salary", "status": "Resolved", "summary": "y"},
]
TRANSCRIPT = "Staff: How can I help?\nCustomer: My salary was cut after my leave complaint."


class _FakeAsyncClient:
    def __init__(self, content: str):
        self.calls: list[dict] = []

        async def create(**kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _reply(**overrides) -> str:
    body = {
        "summary": "Sarah Lim reported a salary deduction after her leave complaint.",
        "issue": "Salary deduction, possible retaliation",
        "linked_records": ["CASE-2026-03117", "CASE-0000-00000"],
        "actions": ["Link to open leave case", "Start salary claim for mediation"],
        "documents_requested": ["WhatsApp screenshot", "Last three payslips"],
        "follow_up": "A case officer will contact her.",
    }
    body.update(overrides)
    return json.dumps(body)


def test_summary_uses_record_and_drops_invented_case_ids(monkeypatch):
    fake = _FakeAsyncClient(_reply())
    monkeypatch.setattr("backend.call_summary.get_async_llm_client", lambda: fake)

    body = asyncio.run(
        compute_call_summary(TRANSCRIPT, {"name": "Sarah Lim", "record_match": "nric_worker_permit_id"}, CASES)
    )

    prompt = fake.calls[0]["messages"][1]["content"]
    assert "CUSTOMER RECORD (verified" in prompt and "CASE-2026-03117" in prompt
    assert body["linked_records"] == ["CASE-2026-03117"]
    assert body["documents_requested"] == ["WhatsApp screenshot", "Last three payslips"]
    assert body["timings"]["total_ms"] >= 0
    assert "fallback" not in body


def test_name_only_match_never_links_cases(monkeypatch):
    fake = _FakeAsyncClient(_reply())
    monkeypatch.setattr("backend.call_summary.get_async_llm_client", lambda: fake)

    body = asyncio.run(compute_call_summary(TRANSCRIPT, {"record_match": "name"}, CASES))

    assert body["linked_records"] == []


def test_bad_or_empty_output_returns_flagged_fallback(monkeypatch):
    for content in ("not json", _reply(summary="")):
        fake = _FakeAsyncClient(content)
        monkeypatch.setattr("backend.call_summary.get_async_llm_client", lambda: fake)
        body = asyncio.run(compute_call_summary(TRANSCRIPT))
        assert body["fallback"] is True and body["error"]


def test_call_summary_endpoint(client, monkeypatch):
    fake = _FakeAsyncClient(_reply())
    monkeypatch.setattr("backend.call_summary.get_async_llm_client", lambda: fake)

    r = client.post(
        "/call-summary",
        json={"context": TRANSCRIPT, "customer_history": CASES, "customer_profile": {"name": "Sarah Lim"}},
    )

    assert r.status_code == 200
    assert r.json()["issue"] == "Salary deduction, possible retaliation"
