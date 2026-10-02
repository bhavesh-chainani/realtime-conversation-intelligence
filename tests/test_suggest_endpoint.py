from __future__ import annotations

import json
from types import SimpleNamespace

from backend import config as cfg


def _completion(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


SINGLE_REPLY = json.dumps(
    {
        "should_suggest": True,
        "suggestions": [
            {
                "type": "Next Steps",
                "topic": "Ask for the payslip.",
                "confidence": 0.8,
                "linked_records": ["CASE-1"],
                "details": {"possibleConversation": "Could you share the payslip?", "priority": "high"},
            }
        ],
    }
)


class _FakeAsyncClient:
    def __init__(self):
        self.calls: list[dict] = []

        async def create(**kwargs):
            self.calls.append(kwargs)
            return _completion(SINGLE_REPLY)

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def test_suggest_accepts_legacy_request_shape(client, monkeypatch):
    monkeypatch.setattr(cfg, "SUGGESTION_PIPELINE", "single")
    fake = _FakeAsyncClient()
    monkeypatch.setattr("backend.suggestion_fast.get_async_llm_client", lambda: fake)

    r = client.post(
        "/suggest",
        json={"context": "Staff: Hello\nCustomer: My salary was cut.", "max_suggestions": 2},
    )

    assert r.status_code == 200
    body = r.json()
    assert len(body["suggestions"]) == 1
    # No history sent, so the model's case ID cannot survive validation.
    assert body["suggestions"][0]["linked_records"] == []


def test_suggest_passes_customer_history_into_prompt(client, monkeypatch):
    monkeypatch.setattr(cfg, "SUGGESTION_PIPELINE", "single")
    fake = _FakeAsyncClient()
    monkeypatch.setattr("backend.suggestion_fast.get_async_llm_client", lambda: fake)

    r = client.post(
        "/suggest",
        json={
            "context": "Staff: Hello\nCustomer: My salary was cut.",
            "customer_profile": {"name": "Sarah Lim", "nric_worker_permit_id": "S1234567A"},
            "customer_history": [
                {"case_id": "CASE-1", "company": "Brightpath", "type": "Leave", "status": "Open", "summary": "x"}
            ],
            "scenario_id": "sarah_lim_brightpath",
            "script_step": "L08",
        },
    )

    assert r.status_code == 200
    assert r.json()["suggestions"][0]["linked_records"] == ["CASE-1"]
    assert "CASE-1 | Brightpath" in fake.calls[0]["messages"][1]["content"]


def test_router_pipeline_still_works(client, monkeypatch):
    monkeypatch.setattr(cfg, "SUGGESTION_PIPELINE", "router")
    router_reply = json.dumps(
        {"should_suggest": True, "confidence": 0.9, "reason": "r", "known_info": ["name"], "missing_info": ["id"]}
    )
    suggestion_reply = json.dumps(
        [{"type": "Verification", "topic": "Ask for NRIC", "confidence": 0.8, "details": {"possibleConversation": "May I have your NRIC?"}}]
    )
    monkeypatch.setattr(
        "backend.router_agent.get_llm_client",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: _completion(router_reply)))
        ),
    )
    monkeypatch.setattr(
        "backend.suggestion_agent.get_llm_client",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: _completion(suggestion_reply)))
        ),
    )

    r = client.post("/suggest", json={"context": "Staff: Hi there\nCustomer: My name is Sarah Lim."})

    assert r.status_code == 200
    body = r.json()
    assert body["router_decision"]["should_suggest"] is True
    assert body["suggestions"][0]["topic"] == "Ask for NRIC"
    assert body["timings"]["pipeline"] == "router"
