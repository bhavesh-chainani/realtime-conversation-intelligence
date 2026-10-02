from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend.customer_data_extractor import extractor


class _FakeCompletionClient:
    def __init__(self, content: str):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content=content)
                        )
                    ]
                )
            )
        )


def test_extract_customer_data_empty_transcript(client):
    response = client.post(
        "/extract-customer-data",
        json={"conversation_transcript": "hi"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "empty"
    assert body["meta"]["captured_count"] == 0
    assert body["meta"]["history_lookup_ready"] is False


def test_extractor_normalizes_partial_customer_data(monkeypatch):
    monkeypatch.setattr(
        "backend.customer_data_extractor.get_llm_client",
        lambda: _FakeCompletionClient(
            '{"name": "  Raja   Kumar  ", "nric_worker_permit_id": " s123 4567a ", "address": "", "purpose_of_call": " Salary dispute with employer "}'
        ),
    )

    body = asyncio.run(
        extractor.extract(
            "Customer: My name is Raja Kumar. Customer: My nric is s123 4567a. Customer: I am calling about a salary dispute with my employer."
        )
    )

    assert body["success"] is True
    assert body["status"] == "partial"
    assert body["data"]["name"] == "Raja Kumar"
    assert body["data"]["nric_worker_permit_id"] == "S1234567A"
    assert body["data"]["address"] is None
    assert body["meta"]["captured_fields"] == [
        "name",
        "nric_worker_permit_id",
        "purpose_of_call",
    ]
    assert body["meta"]["history_lookup_ready"] is True


def test_extract_customer_data_endpoint_complete(monkeypatch, client):
    monkeypatch.setattr(
        "backend.customer_data_extractor.get_llm_client",
        lambda: _FakeCompletionClient(
            '{"name": "Asha Devi", "nric_worker_permit_id": "F7654321Q", "address": "10 Jurong West Street 52", "purpose_of_call": "She is calling about an employer housing dispute."}'
        ),
    )

    response = client.post(
        "/extract-customer-data",
        json={
            "conversation_transcript": "Customer: My name is Asha Devi. Customer: My FIN is F7654321Q. Customer: I stay at 10 Jurong West Street 52. Customer: I am calling about an employer housing dispute."
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "complete"
    assert body["meta"]["captured_count"] == 4
    assert body["meta"]["missing_fields"] == []


def test_extractor_returns_error_for_invalid_json(monkeypatch):
    monkeypatch.setattr(
        "backend.customer_data_extractor.get_llm_client",
        lambda: _FakeCompletionClient("not json"),
    )

    body = asyncio.run(
        extractor.extract("Customer: My name is Raja. Customer: I need help with my case.")
    )

    assert body["success"] is False
    assert body["status"] == "error"
    assert body["data"]["name"] is None
    assert body["meta"]["captured_count"] == 0


def test_extractor_falls_back_to_regex_nric_from_customer_lines(monkeypatch):
    monkeypatch.setattr(
        "backend.customer_data_extractor.get_llm_client",
        lambda: _FakeCompletionClient(
            '{"name": "Sarah Lim", "nric_worker_permit_id": "S one two", "address": null, "purpose_of_call": null}'
        ),
    )

    body = asyncio.run(
        extractor.extract(
            "Staff: Could I have your NRIC?\nCustomer: Sure, it's S, one two three four five six seven, A."
        )
    )

    assert body["data"]["nric_worker_permit_id"] == "S1234567A"
    assert "llm_ms" in body["timings"]
