"""backend.llm.hedged: a second request when the first stalls."""

from __future__ import annotations

import asyncio

import pytest

from backend import config as cfg
from backend.llm import hedged, llm_extra_params


def _calls(*delays_and_results):
    """A make_call that returns the n-th (delay, result) on its n-th call; records cancellations."""
    state = {"n": 0, "cancelled": []}

    async def make_call():
        i = state["n"]
        state["n"] += 1
        delay, result = delays_and_results[i]
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            state["cancelled"].append(i)
            raise
        if isinstance(result, Exception):
            raise result
        return result

    return make_call, state


def test_a_quick_reply_is_not_hedged():
    make_call, state = _calls((0.01, "first"))
    assert asyncio.run(hedged(make_call, 0.2)) == ("first", False)
    assert state["n"] == 1


def test_a_stalled_call_is_beaten_by_the_second_one():
    make_call, state = _calls((1.0, "slow"), (0.01, "fast"))
    assert asyncio.run(hedged(make_call, 0.05)) == ("fast", True)
    assert state["cancelled"] == [0]


def test_the_first_call_can_still_win_after_hedging():
    make_call, state = _calls((0.08, "first"), (1.0, "second"))
    assert asyncio.run(hedged(make_call, 0.05)) == ("first", True)
    assert state["cancelled"] == [1]


def test_an_early_failure_is_retried_at_once():
    make_call, state = _calls((0.01, ValueError("bad JSON")), (0.01, "good"))
    assert asyncio.run(hedged(make_call, 5)) == ("good", True)
    assert state["n"] == 2


def test_one_failure_falls_back_to_the_other_call():
    make_call, _ = _calls((0.08, RuntimeError("boom")), (0.15, "second"))
    assert asyncio.run(hedged(make_call, 0.05)) == ("second", True)


def test_both_failing_raises():
    make_call, _ = _calls((0.08, RuntimeError("one")), (0.1, RuntimeError("two")))
    with pytest.raises(RuntimeError):
        asyncio.run(hedged(make_call, 0.05))


def test_hedging_off():
    make_call, state = _calls((0.1, "only"))
    assert asyncio.run(hedged(make_call, 0)) == ("only", False)
    assert state["n"] == 1


@pytest.mark.parametrize(
    "model,expected",
    [
        ("openai.gpt-5.4-mini", True),
        ("azure.gpt-5-nano", True),
        ("gpt-5.4-mini", True),
        ("openai.o4-mini", True),
        ("openai.global.gpt-5.4-mini", True),
        ("openai.gpt-5.2-chat-latest", False),
        ("openai.gpt-4.1-mini", False),
        ("vertex_ai.gemini-3.5-flash-lite", False),
        ("vertex_ai.anthropic.claude-haiku-4-5", False),
    ],
)
def test_reasoning_effort_only_for_models_that_take_it(model, expected, monkeypatch):
    from backend import config as cfg
    from backend.llm import llm_extra_params, supports_reasoning_effort

    monkeypatch.setattr(cfg, "LLM_REASONING_EFFORT", "none")
    assert supports_reasoning_effort(model) is expected
    assert ("reasoning_effort" in llm_extra_params(model)) is expected


def test_service_tier_goes_only_to_live_calls(monkeypatch):
    monkeypatch.setattr(cfg, "LLM_REASONING_EFFORT", "none")
    monkeypatch.setattr(cfg, "LLM_SERVICE_TIER", "priority")
    assert llm_extra_params("gpt-5.4-mini", live=True) == {
        "reasoning_effort": "none",
        "service_tier": "priority",
    }
    assert llm_extra_params("gpt-5.4-mini") == {"reasoning_effort": "none"}  # e.g. wrap-up
    monkeypatch.setattr(cfg, "LLM_SERVICE_TIER", "")
    assert llm_extra_params("gpt-5.4-mini", live=True) == {"reasoning_effort": "none"}
