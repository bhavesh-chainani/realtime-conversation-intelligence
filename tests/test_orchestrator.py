"""POST /assist orchestration (backend.orchestrator) with fake lookup and agents."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from backend.api import app
from backend.orchestrator import AssistDeps, AssistRequest, get_assist_deps, run_assist

CUSTOMER = {
    "name": "Katherine Liao",
    "contact_number": "+65 9123 4567",
    "email": "katherine.liao@example.com",
}
CUSTOMER_KEY = "id:91234567|katherine.liao@example.com"
CASES = [
    {
        "case_id": "CASE-2026-03117",
        "company": "Brightpath",
        "type": "Leave",
        "status": "Open",
        "summary": "x",
    },
    {
        "case_id": "CASE-2025-10421",
        "company": "Brightpath",
        "type": "Salary",
        "status": "Resolved",
        "summary": "y",
    },
]
PHONE_MATCH = {
    "status": "ok",
    "match_strategy": "contact_number",
    "customer": CUSTOMER,
    "cases": CASES,
    "open_count": 1,
    "history_summary": "Katherine Liao has 2 prior cases.",
}
EMAIL_MATCH = {**PHONE_MATCH, "match_strategy": "email"}
NAME_MATCH = {**PHONE_MATCH, "match_strategy": "name"}
NOT_FOUND = {"status": "not_found", "cases": [], "message": "No prior cases found."}


class FakeLookup:
    def __init__(
        self,
        by_id: dict | None = None,
        by_name: dict | None = None,
        error: Exception | None = None,
    ):
        self.by_id, self.by_name, self.error = by_id or {}, by_name or {}, error
        self.calls: list[tuple[str | None, str | None, str | None]] = []

    def __call__(self, name, phone, email):
        """Like the real service: phone first, then email; a name only when neither was given."""
        self.calls.append((name, phone, email))
        if self.error:
            raise self.error
        if phone or email:
            return self.by_id.get(phone) or self.by_id.get(email) or NOT_FOUND
        return self.by_name.get(name, NOT_FOUND)


class FakeSuggest:
    """Returns at once, or blocks on `gate` for the rounds listed in `block_rounds`."""

    def __init__(self, block_rounds: tuple[int, ...] = ()):
        self.block_rounds = block_rounds
        self.calls: list[dict[str, Any]] = []
        self.cancelled: list[int] = []
        self.gate = asyncio.Event()

    async def __call__(self, transcript, max_suggestions, profile, cases):
        round_ = len(self.calls) + 1
        self.calls.append(
            {"transcript": transcript, "profile": profile, "cases": cases}
        )
        if round_ in self.block_rounds:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled.append(round_)
                raise
        return {"suggestions": [{"topic": f"round {round_}"}], "timings": {"llm_ms": 1}}


def make_extract(
    result: dict | None = None, delay: float = 0, error: Exception | None = None
):
    calls: list[str] = []

    async def extract(transcript):
        calls.append(transcript)
        await asyncio.sleep(delay)
        if error:
            raise error
        return result or {}

    extract.calls = calls
    return extract


def deps(lookup=None, extract=None, suggest=None) -> AssistDeps:
    return AssistDeps(
        lookup=lookup or FakeLookup(),
        extract=extract or make_extract(),
        suggest=suggest or FakeSuggest(),
        debounce_s=0,
        lookup_wait_s=0.2,
    )


def request(*lines: str, **kwargs) -> AssistRequest:
    turns = []
    for line in lines:
        role, text = line.split(": ", 1)
        turns.append({"role": role.lower(), "text": text})
    return AssistRequest(turns=turns, **kwargs)


def run(req: AssistRequest, d: AssistDeps) -> list[dict[str, Any]]:
    async def collect():
        return [event async for event in run_assist(req, d)]

    return asyncio.run(collect())


def kinds(events) -> list[str]:
    out = []
    for e in events:
        detail = e.get("source") or e.get("status") or e.get("stage") or e.get("round")
        out.append(f"{e['type']}:{detail}" if detail is not None else e["type"])
    return out


def test_phone_turn_looks_up_at_once_and_grounds_the_suggestion():
    lookup, suggest = FakeLookup(by_id={"91234567": PHONE_MATCH}), FakeSuggest()
    events = run(
        request(
            "Staff: May I have your phone number?",
            "Customer: Sure, it's nine one two three, four five six seven.",
        ),
        deps(lookup, suggest=suggest),
    )

    assert kinds(events) == [
        "customer:heard",
        "history:loading",
        "history:ok",
        "customer:records",
        "suggesting:1",
        "suggestions:1",
        "done",
    ]
    assert events[0]["patch"] == {"contact_number": "91234567"}
    assert events[1]["lookup_key"] == "id:91234567"
    # The record adds the email, so the key covers both and the next turn does not look up again.
    assert events[2]["lookup_key"] == CUSTOMER_KEY and events[2]["open_count"] == 1
    # Records also re-tag the heard phone number as verified.
    assert events[3]["patch"] == CUSTOMER
    assert lookup.calls == [(None, "91234567", None)]
    call = suggest.calls[0]
    assert call["cases"] == CASES
    assert call["profile"]["record_match"] == "contact_number"
    assert call["transcript"].endswith(
        "Customer: Sure, it's nine one two three, four five six seven."
    )


def test_email_match_is_verified():
    lookup, suggest = (
        FakeLookup(by_id={"katherine.liao@example.com": EMAIL_MATCH}),
        FakeSuggest(),
    )
    events = run(
        request("Customer: It's katherine dot liao at example dot com."),
        deps(lookup, suggest=suggest),
    )
    assert lookup.calls == [(None, None, "katherine.liao@example.com")]
    assert "customer:records" in kinds(events)
    assert suggest.calls[0]["profile"]["record_match"] == "email"


def test_email_heard_after_an_unmatched_phone_looks_up_again():
    lookup = FakeLookup(by_id={"katherine.liao@example.com": EMAIL_MATCH})
    events = run(
        request(
            "Customer: My email is katherine.liao@example.com.",
            customer={"contact_number": "81111111"},
            sources={"contact_number": "heard"},
            history={"lookup_key": "id:81111111", "match_strategy": None, "cases": []},
        ),
        deps(lookup),
    )
    assert lookup.calls == [(None, "81111111", "katherine.liao@example.com")]
    assert next(e for e in events if e["type"] == "history" and e["status"] == "ok")
    # Records beat what was heard: the card now shows the number on file.
    records = next(
        e for e in events if e["type"] == "customer" and e["source"] == "records"
    )
    assert records["patch"]["contact_number"] == "+65 9123 4567"


def test_known_identity_is_not_looked_up_again():
    lookup, suggest = FakeLookup(by_id={"91234567": PHONE_MATCH}), FakeSuggest()
    events = run(
        request(
            "Customer: My employer cut my leave.",
            customer=CUSTOMER,
            sources=dict.fromkeys(CUSTOMER, "records"),
            history={
                "lookup_key": CUSTOMER_KEY,
                "match_strategy": "contact_number",
                "cases": CASES,
            },
        ),
        deps(lookup, suggest=suggest),
    )
    assert lookup.calls == []
    assert suggest.calls[0]["cases"] == CASES
    assert kinds(events)[-2:] == ["suggestions:1", "done"]


def test_name_only_match_is_unverified():
    lookup, suggest = FakeLookup(by_name={"Katherine Liao": NAME_MATCH}), FakeSuggest()
    events = run(
        request("Customer: Hi, my name is Katherine Liao."),
        deps(lookup, suggest=suggest),
    )

    assert lookup.calls == [("Katherine Liao", None, None)]
    assert "customer:records" not in kinds(
        events
    )  # no prefill without a phone / email match
    assert suggest.calls[0]["profile"]["record_match"] == "name"


def test_staff_entered_phone_beats_what_was_heard():
    lookup = FakeLookup()
    events = run(
        request(
            "Customer: It's 9123 4567.",
            customer={"contact_number": "81111111"},
            sources={"contact_number": "manual"},
        ),
        deps(lookup),
    )
    assert "customer:heard" not in kinds(events)
    assert lookup.calls == [(None, "81111111", None)]


def test_phone_found_by_extraction_replaces_the_stale_suggestion():
    lookup = FakeLookup(by_id={"91234567": PHONE_MATCH})
    suggest = FakeSuggest(block_rounds=(1,))
    events = run(
        request("Customer: My employer cut my salary again."),
        deps(lookup, make_extract({"contact_number": "91234567"}), suggest),
    )

    assert kinds(events) == [
        "suggesting:1",
        "customer:ai",
        "history:loading",
        "history:ok",
        "customer:records",
        "suggesting:2",
        "suggestions:2",
        "done",
    ]
    assert suggest.cancelled == [1]
    assert suggest.calls[0]["cases"] == [] and suggest.calls[1]["cases"] == CASES


def test_records_arriving_after_a_suggestion_add_a_second_round():
    lookup = FakeLookup(by_id={"91234567": PHONE_MATCH})
    events = run(
        request("Customer: My employer cut my salary again."),
        deps(lookup, make_extract({"contact_number": "91234567"}, delay=0.05)),
    )
    rounds = [e["round"] for e in events if e["type"] == "suggestions"]
    assert rounds == [1, 2]


def test_extraction_only_runs_while_fields_are_missing():
    extract = make_extract()
    full = {**CUSTOMER, "purpose_of_call": "Leave dispute"}
    run(
        request("Customer: Thanks.", customer=full, sources=dict.fromkeys(full, "ai")),
        deps(extract=extract),
    )
    run(request("Customer: Thanks.", extract=False), deps(extract=extract))
    assert extract.calls == []

    run(request("Customer: My salary was cut."), deps(extract=extract))
    assert extract.calls == ["Customer: My salary was cut."]


def test_failures_are_reported_in_band_and_done_still_comes_last():
    events = run(
        request("Customer: It's 9123 4567."),
        deps(
            FakeLookup(error=RuntimeError("db down")),
            make_extract(error=RuntimeError("llm down")),
        ),
    )
    assert "error:lookup" in kinds(events) and "error:extract" in kinds(events)
    assert (
        next(e for e in events if e["type"] == "history" and e["status"] != "loading")[
            "status"
        ]
        == "error"
    )
    assert "suggestions:1" in kinds(events) and kinds(events)[-1] == "done"


def test_empty_transcript_gets_no_suggestion():
    events = run(AssistRequest(turns=[{"role": "customer", "text": "  "}]), deps())
    assert "suggesting:1" not in kinds(events) and kinds(events)[-1] == "done"


def test_client_disconnect_cancels_pending_work():
    suggest = FakeSuggest(block_rounds=(1,))

    async def scenario():
        stream = run_assist(
            request("Customer: My employer cut my salary again."), deps(suggest=suggest)
        )
        async for event in stream:
            if event["type"] == "suggesting":
                break
        await asyncio.sleep(0)  # let the suggestion task start
        await stream.aclose()
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert suggest.cancelled == [1]


def test_endpoint_streams_ndjson(client):
    lookup = FakeLookup(by_id={"91234567": PHONE_MATCH})
    app.dependency_overrides[get_assist_deps] = lambda: deps(lookup)
    try:
        r = client.post(
            "/assist",
            json={
                "turns": [
                    {
                        "role": "customer",
                        "text": "My number is 9123 4567, and my leave was cut.",
                    }
                ]
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in r.text.splitlines() if line]
    assert events[0] == {
        "type": "customer",
        "source": "heard",
        "patch": {"contact_number": "91234567"},
    }
    assert events[-1]["type"] == "done"
