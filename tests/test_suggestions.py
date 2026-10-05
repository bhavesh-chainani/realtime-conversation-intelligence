"""Suggestion agent (backend.agents.suggestion_agent)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from backend.agents.suggestion_agent import generate_suggestions, suggest_with_fallback

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
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

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
                    "details": {
                        "possibleConversation": "I'll add this to your open case.",
                        "priority": "high",
                    },
                }
            ],
        }
    )


def _use(monkeypatch, fake: _FakeAsyncClient) -> None:
    monkeypatch.setattr(
        "backend.agents.suggestion_agent.get_async_llm_client", lambda: fake
    )


def test_customer_record_is_rendered_into_prompt(monkeypatch):
    fake = _FakeAsyncClient(_model_reply(["CASE-2026-03117"]))
    _use(monkeypatch, fake)

    body = asyncio.run(
        generate_suggestions(
            TRANSCRIPT,
            customer_profile={
                "name": "Katherine Liao",
                "nric_worker_permit_id": "S1234567A",
            },
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

    body = asyncio.run(
        generate_suggestions(
            TRANSCRIPT, customer_profile={"record_match": "name"}, customer_cases=CASES
        )
    )

    assert "NAME ONLY" in fake.user_prompt()
    assert body["suggestions"][0]["linked_records"] == []


def test_hallucinated_case_ids_are_dropped(monkeypatch):
    _use(
        monkeypatch,
        _FakeAsyncClient(_model_reply(["CASE-9999-00000", "CASE-2025-10421"])),
    )

    body = asyncio.run(generate_suggestions(TRANSCRIPT, customer_cases=CASES))

    assert body["suggestions"][0]["linked_records"] == ["CASE-2025-10421"]


def test_agent_can_decline_to_suggest(monkeypatch):
    _use(
        monkeypatch,
        _FakeAsyncClient(json.dumps({"should_suggest": False, "suggestions": []})),
    )

    body = asyncio.run(generate_suggestions(TRANSCRIPT))

    assert body["suggestions"] == [] and body["decision"]["should_suggest"] is False


def test_invalid_json_returns_flagged_fallback(monkeypatch):
    _use(monkeypatch, _FakeAsyncClient("not json at all"))

    body = asyncio.run(suggest_with_fallback(TRANSCRIPT, max_suggestions=2))

    assert body["fallback"] is True
    assert body["error"]
    assert body["suggestions"]
    assert body["timings"]["total_ms"] >= 0
