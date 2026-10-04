"""POST /assist orchestration (backend.orchestrator) with fake lookup and agents."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from backend.api import app
from backend.orchestrator import AssistDeps, AssistRequest, get_assist_deps, run_assist

CUSTOMER = {
    "name": "Katherine Liao",
    "nric_worker_permit_id": "S1234567A",
    "address": "12 Tampines Street 45",
}
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
NRIC_MATCH = {
    "status": "ok",
    "match_strategy": "nric_worker_permit_id",
    "customer": CUSTOMER,
    "cases": CASES,
    "open_count": 1,
    "history_summary": "Katherine Liao has 2 prior cases.",
}
NAME_MATCH = {**NRIC_MATCH, "match_strategy": "name"}
NOT_FOUND = {"status": "not_found", "cases": [], "message": "No prior cases found."}


class FakeLookup:
    def __init__(
        self, by_id: dict | None = None, by_name: dict | None = None, error: Exception | None = None
    ):
        self.by_id, self.by_name, self.error = by_id or {}, by_name or {}, error
        self.calls: list[tuple[str | None, str | None]] = []

    def __call__(self, name, nric):
        self.calls.append((name, nric))
        if self.error:
            raise self.error
        if nric:
            return self.by_id.get(nric, NOT_FOUND)
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
        self.calls.append({"transcript": transcript, "profile": profile, "cases": cases})
        if round_ in self.block_rounds:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled.append(round_)
                raise
        return {"suggestions": [{"topic": f"round {round_}"}], "timings": {"llm_ms": 1}}


def make_extract(result: dict | None = None, delay: float = 0, error: Exception | None = None):
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


def test_nric_turn_looks_up_at_once_and_grounds_the_suggestion():
    lookup, suggest = FakeLookup(by_id={"S1234567A": NRIC_MATCH}), FakeSuggest()
    events = run(
        request(
            "Staff: May I have your NRIC?", "Customer: Sure, it's S one two three four five six seven A."
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
    assert events[0]["patch"] == {"nric_worker_permit_id": "S1234567A"}
    assert events[2]["lookup_key"] == "id:S1234567A" and events[2]["open_count"] == 1
    # Records also re-tag the heard NRIC as verified.
    assert events[3]["patch"] == CUSTOMER
    assert lookup.calls == [(None, "S1234567A")]
    call = suggest.calls[0]
    assert call["cases"] == CASES
    assert call["profile"]["record_match"] == "nric_worker_permit_id"
    assert call["transcript"].endswith("Customer: Sure, it's S one two three four five six seven A.")


def test_known_identity_is_not_looked_up_again():
    lookup, suggest = FakeLookup(by_id={"S1234567A": NRIC_MATCH}), FakeSuggest()
    events = run(
        request(
            "Customer: My employer cut my leave.",
            customer=CUSTOMER,
            sources=dict.fromkeys(CUSTOMER, "records"),
            history={"lookup_key": "id:S1234567A", "match_strategy": "nric_worker_permit_id", "cases": CASES},
        ),
        deps(lookup, suggest=suggest),
    )
    assert lookup.calls == []
    assert suggest.calls[0]["cases"] == CASES
    assert kinds(events)[-2:] == ["suggestions:1", "done"]


def test_name_only_match_is_unverified():
    lookup, suggest = FakeLookup(by_name={"Katherine Liao": NAME_MATCH}), FakeSuggest()
    events = run(request("Customer: Hi, my name is Katherine Liao."), deps(lookup, suggest=suggest))

    assert lookup.calls == [("Katherine Liao", None)]
    assert "customer:records" not in kinds(events)  # no prefill without an NRIC match
    assert suggest.calls[0]["profile"]["record_match"] == "name"


def test_staff_entered_nric_beats_what_was_heard():
    lookup = FakeLookup(by_id={"S1111111A": NOT_FOUND})
    events = run(
        request(
            "Customer: It's S1234567A.",
            customer={"nric_worker_permit_id": "S1111111A"},
            sources={"nric_worker_permit_id": "manual"},
        ),
        deps(lookup),
    )
    assert "customer:heard" not in kinds(events)
    assert lookup.calls == [(None, "S1111111A")]


def test_nric_found_by_extraction_replaces_the_stale_suggestion():
    lookup = FakeLookup(by_id={"S1234567A": NRIC_MATCH})
    suggest = FakeSuggest(block_rounds=(1,))
    events = run(
        request("Customer: My employer cut my salary again."),
        deps(lookup, make_extract({"nric_worker_permit_id": "S1234567A"}), suggest),
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
    lookup = FakeLookup(by_id={"S1234567A": NRIC_MATCH})
    events = run(
        request("Customer: My employer cut my salary again."),
        deps(lookup, make_extract({"nric_worker_permit_id": "S1234567A"}, delay=0.05)),
    )
    rounds = [e["round"] for e in events if e["type"] == "suggestions"]
    assert rounds == [1, 2]


def test_extraction_only_runs_while_fields_are_missing():
    extract = make_extract()
    full = {**CUSTOMER, "purpose_of_call": "Leave dispute"}
    run(request("Customer: Thanks.", customer=full, sources=dict.fromkeys(full, "ai")), deps(extract=extract))
    run(request("Customer: Thanks.", extract=False), deps(extract=extract))
    assert extract.calls == []

    run(request("Customer: My salary was cut."), deps(extract=extract))
    assert extract.calls == ["Customer: My salary was cut."]


def test_failures_are_reported_in_band_and_done_still_comes_last():
    events = run(
        request("Customer: It's S1234567A."),
        deps(FakeLookup(error=RuntimeError("db down")), make_extract(error=RuntimeError("llm down"))),
    )
    assert "error:lookup" in kinds(events) and "error:extract" in kinds(events)
    assert next(e for e in events if e["type"] == "history" and e["status"] != "loading")["status"] == "error"
    assert "suggestions:1" in kinds(events) and kinds(events)[-1] == "done"


def test_empty_transcript_gets_no_suggestion():
    events = run(AssistRequest(turns=[{"role": "customer", "text": "  "}]), deps())
    assert "suggesting:1" not in kinds(events) and kinds(events)[-1] == "done"


def test_client_disconnect_cancels_pending_work():
    suggest = FakeSuggest(block_rounds=(1,))

    async def scenario():
        stream = run_assist(request("Customer: My employer cut my salary again."), deps(suggest=suggest))
        async for event in stream:
            if event["type"] == "suggesting":
                break
        await asyncio.sleep(0)  # let the suggestion task start
        await stream.aclose()
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert suggest.cancelled == [1]


def test_endpoint_streams_ndjson(client):
    lookup = FakeLookup(by_id={"S1234567A": NRIC_MATCH})
    app.dependency_overrides[get_assist_deps] = lambda: deps(lookup)
    try:
        r = client.post(
            "/assist",
            json={"turns": [{"role": "customer", "text": "My NRIC is S1234567A, and my leave was cut."}]},
        )
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in r.text.splitlines() if line]
    assert events[0] == {
        "type": "customer",
        "source": "heard",
        "patch": {"nric_worker_permit_id": "S1234567A"},
    }
    assert events[-1]["type"] == "done"
