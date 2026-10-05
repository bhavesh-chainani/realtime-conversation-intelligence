import assert from "node:assert/strict";
import { test } from "node:test";

import { assistReducer, buildAssistRequest, initialAssistState, type AssistState } from "./assist-state.ts";
import type { AssistEvent, HistoryResult } from "./assist-stream.ts";

const OK_RESULT: HistoryResult = {
  status: "ok",
  lookupKey: "id:91234567",
  matchedOn: "contact_number",
  cases: [
    {
      case_id: "CASE-1",
      company: "Brightpath",
      type: "Leave",
      status: "Open",
      summary: "x",
      opened_on: "",
      next_action: "",
      follow_up_due: "",
    },
  ],
  openCount: 1,
  summary: "1 case",
};
const OK_HISTORY: AssistEvent = { type: "history", ...OK_RESULT };

function streaming(state: AssistState = initialAssistState, gen = 1, committedAt = 100): AssistState {
  return assistReducer(state, { type: "streamStart", gen, committedAt, endedAt: committedAt - 400 });
}

function send(state: AssistState, event: AssistEvent, gen = state.gen, at = 1000): AssistState {
  return assistReducer(state, { type: "event", gen, event, at });
}

test("events fill the caller card, history and suggestion", () => {
  let s = streaming();
  s = send(s, { type: "customer", source: "heard", patch: { contact_number: "91234567" } });
  s = send(s, { type: "history", status: "loading", lookupKey: "id:91234567" });
  assert.equal(s.history.status, "loading");
  s = send(s, OK_HISTORY);
  s = send(s, { type: "customer", source: "records", patch: { name: "Katherine Liao" } });
  s = send(s, { type: "suggesting" });
  assert.equal(s.fetching, true);
  s = send(s, {
    type: "suggestions",
    suggestions: [{ topic: "a" }, { topic: "b" }],
    fallback: false,
    timings: { llm_ms: 5 },
  });
  s = send(s, { type: "done" });

  assert.equal(s.customer.name, "Katherine Liao");
  assert.deepEqual(s.sources, { contact_number: "heard", name: "records" });
  assert.deepEqual([s.history.openCount, s.history.matchedOn], [1, "contact_number"]);
  assert.deepEqual(s.suggestions, [{ topic: "a" }]);
  // Measured from when the caller stopped speaking: 400 ms waiting for the speaker, then the agents.
  assert.deepEqual(s.suggestionMeta, { origin: "live", latencyMs: 1300, speakerMs: 400, llmMs: 5, model: undefined });
  assert.equal(s.fetching, false);
});

test("events from an older stream are ignored", () => {
  const s = streaming(streaming(), 2);
  assert.equal(send(s, OK_HISTORY, 1), s);
});

test("after a staff identity edit, stale lookup results are dropped but suggestions still land", () => {
  let s = streaming();
  s = assistReducer(s, { type: "manualEdit", field: "contact_number", value: "94567890" });
  assert.equal(s.history.status, "idle");
  assert.equal(send(s, OK_HISTORY), s);
  assert.equal(send(s, { type: "customer", source: "records", patch: { name: "Katherine Liao" } }), s);
  s = send(s, { type: "suggestions", suggestions: [{ topic: "a" }], fallback: false, timings: {} });
  assert.deepEqual(s.suggestions, [{ topic: "a" }]);
});

test("an empty answer keeps the current suggestion", () => {
  let s = send(streaming(), {
    type: "suggestions",
    suggestions: [{ topic: "a" }],
    fallback: false,
    timings: {},
  });
  s = send(s, { type: "suggestions", suggestions: [], fallback: true, timings: {} });
  assert.deepEqual(s.suggestions, [{ topic: "a" }]);
});

test("an abandoned stream puts the history status back", () => {
  let s = streaming();
  s = send(s, { type: "history", status: "loading", lookupKey: "id:91234567" });
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
  s = send(s, { type: "customer", source: "heard", patch: { contact_number: "91234567" } });
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
    customer: { contact_number: "91234567" },
    sources: { contact_number: "heard" },
    history: { lookup_key: "id:91234567", match_strategy: "contact_number", cases: OK_RESULT.cases, status: "ok" },
    previous_suggestions: [],
    extract: false,
    max_suggestions: 1,
  });
});

test("request carries the suggestion staff can see, so the agent does not repeat it", () => {
  const s = send(streaming(), {
    type: "suggestions",
    suggestions: [{ topic: "Identify the caller", details: { possibleConversation: "May I have your name?" } }],
    fallback: false,
    timings: {},
  });
  assert.deepEqual(buildAssistRequest(s, [], true).previous_suggestions, ["May I have your name?"]);
  assert.deepEqual(buildAssistRequest(streaming(), [], true).previous_suggestions, []);
});
