"""Suggestion agent (backend.agents.suggestion_agent)."""

from __future__ import annotations

import asyncio
import json
import time
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


class _FakeStream:
    """A streamed completion: `content` in small pieces, then a usage chunk, like the OpenAI SDK."""

    def __init__(self, content: str, size: int = 7, first_delay: float = 0):
        self.pieces = [content[i : i + size] for i in range(0, len(content), size)]
        self.first_delay = first_delay
        self.closed = False

    async def _chunks(self):
        await asyncio.sleep(self.first_delay)
        for piece in self.pieces:
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=piece))], usage=None)
        usage = SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=3328))
        yield SimpleNamespace(choices=[], usage=usage)

    def __aiter__(self):
        return self._chunks()

    async def close(self):
        self.closed = True


class _FakeAsyncClient:
    def __init__(self, content: str):
        self.calls: list[dict] = []
        self.streams: list[_FakeStream] = []

        async def create(**kwargs):
            self.calls.append(kwargs)
            self.streams.append(_FakeStream(content))
            return self.streams[-1]

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    def with_options(self, **_):
        return self

    def user_prompt(self) -> str:
        return self.calls[-1]["messages"][1]["content"]


def _model_reply(linked: list[str]) -> str:
    return json.dumps(
        {
            "suggestions": [
                {
                    "type": "Case Linking",
                    "topic": "Link to the open leave case.",
                    "confidence": 0.9,
                    "linked_records": linked,
                    "details": {
                        "possibleConversation": "I'll add this to your open case.",
                        "priority": "high",
                    },
                }
            ],
        }
    )


def _use(monkeypatch, fake: _FakeAsyncClient) -> None:
    monkeypatch.setattr("backend.agents.suggestion_agent.get_suggestion_llm_client", lambda: fake)


def test_customer_record_is_rendered_into_prompt(monkeypatch):
    fake = _FakeAsyncClient(_model_reply(["CASE-2026-03117"]))
    _use(monkeypatch, fake)

    body = asyncio.run(
        generate_suggestions(
            TRANSCRIPT,
            customer_profile={
                "name": "Katherine Liao",
                "contact_number": "91234567",
                "record_match": "contact_number",
            },
            customer_cases=CASES,
        )
    )

    prompt = fake.user_prompt()
    assert "CUSTOMER RECORD (verified" in prompt
    assert "CASE-2026-03117" in prompt and "OPEN (open)" in prompt
    assert "Prior cases (2; 1 open)" in prompt
    assert body["suggestions"][0]["linked_records"] == ["CASE-2026-03117"]
    assert "llm_ms" in body["timings"]


def test_without_history_prompt_says_not_retrieved(monkeypatch):
    fake = _FakeAsyncClient(_model_reply([]))
    _use(monkeypatch, fake)

    body = asyncio.run(generate_suggestions(TRANSCRIPT))

    prompt = fake.user_prompt()
    assert "CUSTOMER RECORD: none" in prompt
    assert "CALLER CARD" in prompt and "full name, contact number and email address" in prompt
    assert "SUGGESTION STAFF CAN SEE NOW (from the previous turn):\nnone" in prompt
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
        _FakeAsyncClient(json.dumps({"suggestions": []})),
    )

    body = asyncio.run(generate_suggestions(TRANSCRIPT))

    assert body["suggestions"] == []


def test_invalid_json_returns_flagged_fallback(monkeypatch):
    _use(monkeypatch, _FakeAsyncClient("not json at all"))

    body = asyncio.run(suggest_with_fallback(TRANSCRIPT, max_suggestions=2))

    assert body["fallback"] is True
    assert body["suggestions"]
    assert body["timings"]["total_ms"] >= 0


def test_previous_suggestion_and_caller_card_are_in_the_prompt(monkeypatch):
    fake = _FakeAsyncClient(_model_reply([]))
    _use(monkeypatch, fake)

    asyncio.run(
        generate_suggestions(
            TRANSCRIPT,
            customer_profile={
                "name": "Katherine Liao",
                "contact_number": "81112222",
                "lookup_status": "not_found",
            },
            previous_suggestions=["May I have your full name and a contact number?"],
        )
    )

    prompt = fake.user_prompt()
    assert "Name: Katherine Liao | Contact number: 81112222 | Email: not given" in prompt
    assert "Ask for the caller's email address" in prompt
    assert "- May I have your full name and a contact number?" in prompt


def test_prompt_has_today_and_the_service_guide(monkeypatch):
    fake = _FakeAsyncClient(_model_reply([]))
    _use(monkeypatch, fake)
    monkeypatch.setattr(
        "backend.issue_guides._guides",
        [
            {
                "issue_type": "Unpaid or late salary",
                "route": "File a salary claim with TADM.",
            }
        ],
    )

    asyncio.run(generate_suggestions(TRANSCRIPT))

    assert fake.user_prompt().startswith("TODAY: ")
    assert "CALENDAR (next 14 days): " in fake.user_prompt()
    # The guide is fixed for the process, so it sits in the system prompt (a cacheable prefix).
    system = fake.calls[-1]["messages"][0]["content"]
    assert "## Unpaid or late salary" in system and "File a salary claim with TADM." in system
    assert "SERVICE GUIDE" not in fake.user_prompt()


def test_a_stalled_gateway_gets_the_fallback_by_the_deadline(monkeypatch):
    from backend import config as cfg

    class Stalled(_FakeAsyncClient):
        def __init__(self):
            super().__init__("{}")

            async def create(**kwargs):
                self.calls.append(kwargs)
                await asyncio.sleep(5)

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    stalled = Stalled()
    _use(monkeypatch, stalled)
    monkeypatch.setattr(cfg, "SUGGESTION_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(cfg, "SUGGESTION_HEDGE_FIRST_TOKEN_MS", 50)

    started = time.perf_counter()
    body = asyncio.run(suggest_with_fallback(TRANSCRIPT, 1))

    assert body["fallback"] is True and body["suggestions"]
    assert time.perf_counter() - started < 1
    assert len(stalled.calls) == 2  # hedged once before the deadline


def test_malformed_json_is_retried_instead_of_falling_back(monkeypatch):
    replies = iter(['{"suggestions": [\n  {topic: ', _model_reply([])])

    class Flaky(_FakeAsyncClient):
        def __init__(self):
            super().__init__("{}")

            async def create(**kwargs):
                self.calls.append(kwargs)
                return _FakeStream(next(replies))

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    flaky = Flaky()
    _use(monkeypatch, flaky)
    body = asyncio.run(suggest_with_fallback(TRANSCRIPT, 1))
    assert not body.get("fallback") and body["suggestions"]
    assert len(flaky.calls) == 2


def test_wording_streams_to_on_draft_before_the_answer(monkeypatch):
    reply = json.dumps(
        {
            "suggestions": [
                {
                    "possibleConversation": "When did they last pay you in full?",
                    "type": "Information Gathering",
                    "topic": "Ask when the caller was last paid.",
                    "confidence": 0.8,
                    "linked_records": [],
                    "priority": "high",
                }
            ]
        }
    )
    fake = _FakeAsyncClient(reply)
    _use(monkeypatch, fake)
    drafts: list[str] = []

    body = asyncio.run(generate_suggestions(TRANSCRIPT, on_draft=drafts.append))

    assert len(drafts) > 2 and drafts[-1] == "When did they last pay you in full?"
    assert all(b.startswith(a) for a, b in zip(drafts, drafts[1:], strict=False))
    suggestion = body["suggestions"][0]
    assert suggestion["details"] == {"possibleConversation": drafts[-1], "priority": "high"}
    assert body["timings"]["cached_tokens"] == 3328
    assert fake.calls[0]["stream"] is True and all(s.closed for s in fake.streams)


def test_a_slow_first_token_is_hedged_and_the_loser_closed(monkeypatch):
    from backend import config as cfg

    class SlowFirst(_FakeAsyncClient):
        def __init__(self):
            super().__init__("{}")

            async def create(**kwargs):
                self.calls.append(kwargs)
                delay = 5 if len(self.calls) == 1 else 0
                self.streams.append(_FakeStream(_model_reply([]), first_delay=delay))
                return self.streams[-1]

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    slow = SlowFirst()
    _use(monkeypatch, slow)
    monkeypatch.setattr(cfg, "SUGGESTION_HEDGE_FIRST_TOKEN_MS", 50)

    started = time.perf_counter()
    body = asyncio.run(generate_suggestions(TRANSCRIPT))

    assert time.perf_counter() - started < 1
    assert body["suggestions"] and body["timings"]["hedged"] is True
    assert len(slow.calls) == 2 and all(s.closed for s in slow.streams)


def test_the_hedge_can_go_to_a_backup_model_on_the_other_host(monkeypatch):
    from backend import config as cfg

    class Slow(_FakeAsyncClient):
        def __init__(self):
            super().__init__("{}")

            async def create(**kwargs):
                self.calls.append(kwargs)
                self.streams.append(_FakeStream(_model_reply([]), first_delay=5))
                return self.streams[-1]

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    fast_host, backup_host = Slow(), _FakeAsyncClient(_model_reply([]))
    _use(monkeypatch, fast_host)
    monkeypatch.setattr("backend.agents.suggestion_agent.get_async_llm_client", lambda: backup_host)
    monkeypatch.setattr(cfg, "SUGGESTION_HEDGE_FIRST_TOKEN_MS", 50)
    monkeypatch.setattr(cfg, "SUGGESTION_HEDGE_MODEL", "gpt-5.4-mini")

    body = asyncio.run(generate_suggestions(TRANSCRIPT))

    assert body["suggestions"] and body["timings"]["hedged"] is True
    assert body["timings"]["model"] == "gpt-5.4-mini"
    assert [c["model"] for c in backup_host.calls] == ["gpt-5.4-mini"] and len(fast_host.calls) == 1
    assert all(s.closed for s in fast_host.streams)
