import assert from "node:assert/strict";
import { test } from "node:test";

import { assistReducer, buildAssistRequest, initialAssistState, type AssistState } from "./assist-state.ts";
import type { AssistEvent, HistoryResult } from "./assist-stream.ts";

const OK_RESULT: HistoryResult = {
  status: "ok",
  lookupKey: "id:S1234567A",
  matchedOn: "nric_worker_permit_id",
  cases: [{ case_id: "CASE-1", company: "Brightpath", type: "Leave", status: "Open", summary: "x" }],
  openCount: 1,
  summary: "1 case",
};
const OK_HISTORY: AssistEvent = { type: "history", ...OK_RESULT };

function streaming(state: AssistState = initialAssistState, gen = 1, committedAt = 100): AssistState {
  return assistReducer(state, { type: "streamStart", gen, committedAt });
}

function send(state: AssistState, event: AssistEvent, gen = state.gen, at = 1000): AssistState {
  return assistReducer(state, { type: "event", gen, event, at });
}

test("events fill the caller card, history and suggestion", () => {
  let s = streaming();
  s = send(s, { type: "customer", source: "heard", patch: { nric_worker_permit_id: "S1234567A" } });
  s = send(s, { type: "history", status: "loading", lookupKey: "id:S1234567A" });
  assert.equal(s.history.status, "loading");
  s = send(s, OK_HISTORY);
  s = send(s, { type: "customer", source: "records", patch: { name: "Katherine Liao" } });
  s = send(s, { type: "suggesting", round: 1 });
  assert.equal(s.fetching, true);
  s = send(s, {
    type: "suggestions",
    round: 1,
    suggestions: [{ topic: "a" }, { topic: "b" }],
    fallback: false,
    timings: { llm_ms: 5 },
  });
  s = send(s, { type: "done" });

  assert.equal(s.customer.name, "Katherine Liao");
  assert.deepEqual(s.sources, { nric_worker_permit_id: "heard", name: "records" });
  assert.deepEqual(s.history.meta, { openCount: 1, matchedOn: "nric_worker_permit_id" });
  assert.deepEqual(s.suggestions, [{ topic: "a" }]);
  assert.deepEqual(s.suggestionMeta, { origin: "live", latencyMs: 900, llmMs: 5, model: undefined });
  assert.equal(s.fetching, false);
});

test("events from an older stream are ignored", () => {
  const s = streaming(streaming(), 2);
  assert.equal(send(s, OK_HISTORY, 1), s);
});

test("after a staff identity edit, stale lookup results are dropped but suggestions still land", () => {
  let s = streaming();
  s = assistReducer(s, { type: "manualEdit", field: "nric_worker_permit_id", value: "S7612094B" });
  assert.equal(s.history.status, "idle");
  assert.equal(send(s, OK_HISTORY), s);
  assert.equal(send(s, { type: "customer", source: "records", patch: { name: "Katherine Liao" } }), s);
  s = send(s, { type: "suggestions", round: 1, suggestions: [{ topic: "a" }], fallback: false, timings: {} });
  assert.deepEqual(s.suggestions, [{ topic: "a" }]);
});

test("an empty answer keeps the current suggestion", () => {
  let s = send(streaming(), {
    type: "suggestions",
    round: 1,
    suggestions: [{ topic: "a" }],
    fallback: false,
    timings: {},
  });
  s = send(s, { type: "suggestions", round: 2, suggestions: [], fallback: true, timings: {} });
  assert.deepEqual(s.suggestions, [{ topic: "a" }]);
});

test("an abandoned stream puts the history status back", () => {
  let s = streaming();
  s = send(s, { type: "history", status: "loading", lookupKey: "id:S1234567A" });
  s = assistReducer(s, { type: "streamEnd", gen: 1 });
  assert.equal(s.history.status, "idle");
});

test("manual lookup results apply only to the latest lookup", () => {
  let s = assistReducer(initialAssistState, { type: "manualLookupStart" });
  const result = OK_RESULT;
  const stale = assistReducer(s, { type: "manualLookupResult", epoch: s.epoch - 1, result, prefill: null });
  assert.equal(stale, s);
  s = assistReducer(s, { type: "manualLookupResult", epoch: s.epoch, result, prefill: { name: "Katherine Liao" } });
  assert.equal(s.history.status, "ok");
  assert.deepEqual(s.sources, { name: "records" });
});

test("request carries the profile, sources and last lookup", () => {
  let s = send(streaming(), OK_HISTORY);
  s = send(s, { type: "customer", source: "heard", patch: { nric_worker_permit_id: "S1234567A" } });
  const turns = [
    {
      id: "1",
      text: "Hi",
      speakerLabel: null,
      role: "customer" as const,
      roleSource: "diarization" as const,
      committedAt: 0,
    },
  ];
  assert.deepEqual(buildAssistRequest(s, turns, false), {
    turns: [{ role: "customer", text: "Hi" }],
    customer: { nric_worker_permit_id: "S1234567A" },
    sources: { nric_worker_permit_id: "heard" },
    history: { lookup_key: "id:S1234567A", match_strategy: "nric_worker_permit_id", cases: OK_RESULT.cases },
    extract: false,
    max_suggestions: 1,
  });
});
