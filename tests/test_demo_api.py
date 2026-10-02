from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import config as cfg
from backend import demo_cache
from backend.demo_api import router as demo_router

SCENARIO = {
    "id": "unit_scenario",
    "title": "Unit scenario",
    "keyterms": ["Brightpath", "brightpath", "Tampines"],
    "stt_prompt": "A test call.",
    "lines": [
        {"id": "L01", "role": "staff", "text": "May I have your name?"},
        {"id": "L02", "role": "customer", "text": "My name is Sarah Lim."},
        {"id": "L03", "role": "staff", "text": "And your NRIC?"},
        {"id": "L04", "role": "customer", "text": "It's S1234567A."},
    ],
}
CASES = [{"case_id": "CASE-1", "company": "Brightpath", "type": "Leave", "status": "Open", "summary": "x"}]


@pytest.fixture
def demo_env(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "unit_scenario.json").write_text(json.dumps(SCENARIO))
    monkeypatch.setattr(cfg, "DEMO_SCRIPTS_DIR", scripts)
    monkeypatch.setattr(cfg, "DEMO_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(demo_cache, "_memory_cache", {})

    lookups: list[tuple] = []

    class _FakeHistory:
        def lookup(self, name, nric):
            lookups.append((name, nric))
            match = "nric_worker_permit_id" if nric else "name"
            return {
                "status": "ok",
                "match_strategy": match,
                "customer": {"name": "Sarah Lim", "nric_worker_permit_id": "S1234567A", "address": "12 Tampines"},
                "cases": CASES,
            }

    monkeypatch.setattr(demo_cache, "customer_history_service", _FakeHistory())
    return lookups


@pytest.fixture
def demo_client():
    app = FastAPI()
    app.include_router(demo_router)
    return TestClient(app)


def test_scenarios_are_listed_and_served(demo_env, demo_client):
    listed = demo_client.get("/demo/scenarios").json()
    assert listed == [{"id": "unit_scenario", "title": "Unit scenario", "description": ""}]
    assert demo_client.get("/demo/scenarios/unit_scenario").json()["lines"][1]["id"] == "L02"
    assert demo_client.get("/demo/scenarios/nope").status_code == 404
    assert demo_client.get("/demo/scenarios/..%2Fsecrets").status_code == 404


def test_keyterms_are_merged_deduped_and_capped(demo_env, monkeypatch):
    monkeypatch.setattr(cfg, "ASSEMBLYAI_KEYTERMS", ["Tampines"] + [f"term{i}" for i in range(120)])
    keyterms, prompt = demo_cache.scenario_stt_config("unit_scenario")
    assert len(keyterms) == 100
    assert keyterms[0] == "Tampines"
    assert sum(1 for k in keyterms if k.lower() == "tampines") == 1
    assert prompt == "A test call."


def test_known_context_mirrors_frontend_quick_path(demo_env):
    context = demo_cache.known_context_by_line(SCENARIO["lines"])
    assert context["L01"] == ({}, [])
    profile_l02, cases_l02 = context["L02"]
    assert profile_l02["record_match"] == "name" and cases_l02 == CASES
    profile_l04, _ = context["L04"]
    assert profile_l04["record_match"] == "nric_worker_permit_id"
    assert profile_l04["address"] == "12 Tampines"
    assert demo_env == [("Sarah Lim", None), (None, "S1234567A")]


def test_cache_build_covers_every_customer_line_and_detects_staleness(demo_env, monkeypatch):
    calls: list[dict] = []

    async def fake_compute(context, max_suggestions=2, customer_profile=None, customer_cases=None, pipeline=None):
        calls.append({"context": context, "cases": customer_cases, "pipeline": pipeline})
        return {"suggestions": [{"topic": context.splitlines()[-1]}], "timings": {"total_ms": 1}}

    async def fake_summary(context, profile=None, cases=None):
        calls.append({"context": context, "cases": cases, "pipeline": "wrapup"})
        return {"summary": "Sarah called about leave.", "linked_records": ["CASE-1"], "timings": {}}

    monkeypatch.setattr(demo_cache, "compute_suggestions", fake_compute)
    monkeypatch.setattr(demo_cache, "compute_call_summary", fake_summary)

    status = asyncio.run(demo_cache.build_cache("unit_scenario"))

    assert status["built"] and status["fresh"]
    # Two customer lines + the end-of-call wrap-up.
    assert status["steps"] == status["total"] == 3
    assert {c["pipeline"] for c in calls} == {"single", "wrapup"}
    cache = demo_cache.get_cache("unit_scenario")
    assert cache["steps"]["_wrapup"]["summary"] == "Sarah called about leave."
    wrapup_call = next(c for c in calls if c["pipeline"] == "wrapup")
    assert wrapup_call["context"].endswith("Customer: It's S1234567A.") and wrapup_call["cases"] == CASES
    assert cache["steps"]["L04"]["suggestions"][0]["topic"] == "Customer: It's S1234567A."
    assert (cfg.DEMO_CACHE_DIR / "unit_scenario.json").is_file()

    monkeypatch.setattr(cfg, "LLM_REASONING_EFFORT", "something-else")
    assert demo_cache.cache_status("unit_scenario")["fresh"] is False


def test_failed_cache_steps_are_skipped(demo_env, monkeypatch):
    async def failing_compute(*_, **__):
        return {"suggestions": [], "fallback": True, "error": "boom"}

    monkeypatch.setattr(demo_cache, "compute_suggestions", failing_compute)
    monkeypatch.setattr(demo_cache, "compute_call_summary", failing_compute)

    status = asyncio.run(demo_cache.build_cache("unit_scenario"))
    assert status["steps"] == 0 and status["total"] == 3


def test_token_endpoint_uses_u3_model_and_prompt_for_scenarios(demo_env, monkeypatch, client):
    from backend import api

    async def fake_token(expires_in_seconds=300):
        return "tok"

    monkeypatch.setattr(api, "create_streaming_token", fake_token)
    monkeypatch.setattr(api, "DEMO_MODE", True)
    monkeypatch.setattr(cfg, "ASSEMBLYAI_SPEECH_MODEL", "")
    monkeypatch.setattr(cfg, "DEMO_SPEECH_MODEL", "u3-rt-pro")

    body = client.get("/assemblyai-token?scenario=unit_scenario").json()
    assert body["speech_model"] == "u3-rt-pro"
    assert body["prompt"] == "A test call."
    assert "Brightpath" in body["keyterms_prompt"]

    # A non-u3 model must never receive a prompt (AssemblyAI rejects the session).
    monkeypatch.setattr(cfg, "DEMO_SPEECH_MODEL", "universal-streaming-english")
    body = client.get("/assemblyai-token?scenario=unit_scenario").json()
    assert "prompt" not in body

    # Without a scenario nothing demo-specific is added.
    body = client.get("/assemblyai-token").json()
    assert "prompt" not in body and "speech_model" not in body
