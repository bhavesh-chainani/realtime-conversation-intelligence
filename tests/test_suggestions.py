"""Principal agent (backend.suggestion_agent) and the POST /suggest endpoint."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from backend.suggestion_agent import generate_suggestions
from backend.suggestions import compute_suggestions

CASES = [
    {
        "case_id": "CASE-2026-03117",
        "company": "Brightpath Logistics Pte Ltd",
        "type": "Leave entitlement",
        "status": "Open",
        "summary": "Annual leave forfeited without notice.",
    },
    {
        "case_id": "CASE-2025-10421",
        "company": "Brightpath Logistics Pte Ltd",
        "type": "Salary dispute",
        "status": "Resolved",
        "summary": "Unpaid overtime; employer paid SGD 1,840 after mediation.",
    },
]
TRANSCRIPT = "Staff: How can I help?\nCustomer: My salary from Brightpath was cut."


class _FakeAsyncClient:
    def __init__(self, content: str):
        self.calls: list[dict] = []

        async def create(**kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    def user_prompt(self) -> str:
        return self.calls[-1]["messages"][1]["content"]


def _model_reply(linked: list[str]) -> str:
    return json.dumps(
        {
            "should_suggest": True,
            "suggestions": [
                {
                    "type": "Case Linking",
                    "topic": "Link to the open leave case.",
                    "confidence": 0.9,
                    "linked_records": linked,
                    "source": "history",
                    "details": {"possibleConversation": "I'll add this to your open case.", "priority": "high"},
                }
            ],
        }
    )


def _use(monkeypatch, fake: _FakeAsyncClient) -> None:
    monkeypatch.setattr("backend.suggestion_agent.get_async_llm_client", lambda: fake)


# --- agent -----------------------------------------------------------------


def test_customer_record_is_rendered_into_prompt(monkeypatch):
    fake = _FakeAsyncClient(_model_reply(["CASE-2026-03117"]))
    _use(monkeypatch, fake)

    body = asyncio.run(
        generate_suggestions(
            TRANSCRIPT,
            customer_profile={"name": "Katherine Liao", "nric_worker_permit_id": "S1234567A"},
            customer_cases=CASES,
        )
    )

    prompt = fake.user_prompt()
    assert "CUSTOMER RECORD (verified" in prompt
    assert "CASE-2026-03117" in prompt and "OPEN (open)" in prompt
    assert "Prior cases (2; 1 open)" in prompt
    assert body["suggestions"][0]["linked_records"] == ["CASE-2026-03117"]
    assert body["suggestions"][0]["source"] == "history"
    assert "llm_ms" in body["timings"]


def test_without_history_prompt_says_not_retrieved(monkeypatch):
    fake = _FakeAsyncClient(_model_reply([]))
    _use(monkeypatch, fake)

    body = asyncio.run(generate_suggestions(TRANSCRIPT))

    assert "not yet retrieved" in fake.user_prompt()
    assert body["suggestions"][0]["source"] == "conversation"


def test_name_only_match_is_flagged_unverified(monkeypatch):
    fake = _FakeAsyncClient(_model_reply(["CASE-2026-03117"]))
    _use(monkeypatch, fake)

    body = asyncio.run(generate_suggestions(TRANSCRIPT, customer_profile={"record_match": "name"}, customer_cases=CASES))

    assert "NAME ONLY" in fake.user_prompt()
    assert body["suggestions"][0]["linked_records"] == []


def test_hallucinated_case_ids_are_dropped(monkeypatch):
    _use(monkeypatch, _FakeAsyncClient(_model_reply(["CASE-9999-00000", "CASE-2025-10421"])))

    body = asyncio.run(generate_suggestions(TRANSCRIPT, customer_cases=CASES))

    assert body["suggestions"][0]["linked_records"] == ["CASE-2025-10421"]


def test_agent_can_decline_to_suggest(monkeypatch):
    _use(monkeypatch, _FakeAsyncClient(json.dumps({"should_suggest": False, "suggestions": []})))

    body = asyncio.run(generate_suggestions(TRANSCRIPT))

    assert body["suggestions"] == [] and body["decision"]["should_suggest"] is False


def test_invalid_json_returns_flagged_fallback(monkeypatch):
    _use(monkeypatch, _FakeAsyncClient("not json at all"))

    body = asyncio.run(compute_suggestions(TRANSCRIPT, max_suggestions=2))

    assert body["fallback"] is True
    assert body["error"]
    assert body["suggestions"]
    assert body["timings"]["total_ms"] >= 0


# --- endpoint --------------------------------------------------------------


def test_suggest_without_history_drops_case_ids(client, monkeypatch):
    _use(monkeypatch, _FakeAsyncClient(_model_reply(["CASE-1"])))

    r = client.post("/suggest", json={"context": "Staff: Hello\nCustomer: My salary was cut.", "max_suggestions": 2})

    assert r.status_code == 200
    body = r.json()
    assert len(body["suggestions"]) == 1
    assert "fallback" not in body
    assert body["timings"]["total_ms"] >= 0
    # No history sent, so the model's case ID cannot survive validation.
    assert body["suggestions"][0]["linked_records"] == []


def test_suggest_passes_customer_history_into_prompt(client, monkeypatch):
    fake = _FakeAsyncClient(_model_reply(["CASE-1"]))
    _use(monkeypatch, fake)

    r = client.post(
        "/suggest",
        json={
            "context": "Staff: Hello\nCustomer: My salary was cut.",
            "customer_profile": {"name": "Katherine Liao", "nric_worker_permit_id": "S1234567A"},
            "customer_history": [
                {"case_id": "CASE-1", "company": "Brightpath", "type": "Leave", "status": "Open", "summary": "x"}
            ],
        },
    )

    assert r.status_code == 200
    assert r.json()["suggestions"][0]["linked_records"] == ["CASE-1"]
    assert "CASE-1 | Brightpath" in fake.user_prompt()
