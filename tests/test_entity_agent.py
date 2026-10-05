from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.agents.entity_agent import extract_entities


class _FakeAsyncClient:
    def __init__(self, content: str):
        self.calls: list[dict] = []

        async def create(**kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _use(monkeypatch, content: str) -> _FakeAsyncClient:
    fake = _FakeAsyncClient(content)
    monkeypatch.setattr(
        "backend.agents.entity_agent.get_async_llm_client", lambda: fake
    )
    return fake


def test_short_transcript_skips_the_llm(monkeypatch):
    fake = _use(monkeypatch, "{}")
    assert asyncio.run(extract_entities("hi")) == dict.fromkeys(
        ["name", "contact_number", "email", "purpose_of_call"]
    )
    assert fake.calls == []


def test_values_are_normalized_and_placeholders_dropped(monkeypatch):
    fake = _use(
        monkeypatch,
        '{"name": "  Raja   Kumar  ", "contact_number": " +65 8234 5678 ", "email": "N/A",'
        ' "purpose_of_call": " Salary dispute with employer "}',
    )

    data = asyncio.run(
        extract_entities(
            "Customer: My name is Raja Kumar, my number is 8234 5678. Salary dispute."
        )
    )

    assert data == {
        "name": "Raja Kumar",
        "contact_number": "82345678",
        "email": None,
        "purpose_of_call": "Salary dispute with employer",
    }
    call = fake.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert "Customer: My name is Raja Kumar" in call["messages"][1]["content"]
    assert "Customer lines" in call["messages"][0]["content"]


def test_malformed_contacts_fall_back_to_regex_on_customer_lines(monkeypatch):
    _use(
        monkeypatch,
        '{"name": "Katherine Liao", "contact_number": "nine one two", "email": "katherine at example"}',
    )

    data = asyncio.run(
        extract_entities(
            "Staff: Could I have your number and email?\n"
            "Customer: Sure, nine one two three, four five six seven. katherine dot liao at example dot com."
        )
    )

    assert data["contact_number"] == "91234567"
    assert data["email"] == "katherine.liao@example.com"
    assert data["name"] == "Katherine Liao"


def test_malformed_contacts_are_kept_for_staff_when_nothing_better_is_heard(
    monkeypatch,
):
    _use(monkeypatch, '{"contact_number": "9123 45", "email": "Katherine At Example"}')
    data = asyncio.run(extract_entities("Customer: It's nine one two three four five."))
    assert data["contact_number"] == "9123 45"
    assert data["email"] == "Katherine At Example"


def test_foreign_script_values_are_blanked(monkeypatch):
    _use(monkeypatch, '{"name": "Katherine Liao", "purpose_of_call": "بشأن salary"}')
    data = asyncio.run(
        extract_entities("Customer: I'm Katherine Liao, calling about my salary.")
    )
    assert data["purpose_of_call"] is None and data["name"] == "Katherine Liao"


def test_invalid_json_raises(monkeypatch):
    _use(monkeypatch, "not json")
    with pytest.raises(ValueError):
        asyncio.run(
            extract_entities("Customer: My name is Raja. I need help with my case.")
        )
