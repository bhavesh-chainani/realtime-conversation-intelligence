import assert from "node:assert/strict";
import { test } from "node:test";

import { NdjsonDecoder, parseAssistEvent, parseHistoryResult } from "./assist-stream.ts";

test("decoder joins lines split across chunks and skips blank or malformed lines", () => {
  const d = new NdjsonDecoder();
  assert.deepEqual(d.push('{"type":"sugg'), []);
  assert.deepEqual(d.push('esting","round":1}\n\nnot json\n{"type":"do'), [{ type: "suggesting", round: 1 }]);
  assert.deepEqual(d.push('ne"}\r\n'), [{ type: "done" }]);
  assert.deepEqual(d.push('{"a":1}'), []);
  assert.deepEqual(d.flush(), [{ a: 1 }]);
});

test("multi-byte characters split across network chunks survive TextDecoder streaming", () => {
  const bytes = new TextEncoder().encode('{"type":"error","stage":"x","message":"café"}\n');
  const text = new TextDecoder();
  const d = new NdjsonDecoder();
  const out = [...d.push(text.decode(bytes.slice(0, 44), { stream: true })), ...d.push(text.decode(bytes.slice(44)))];
  assert.deepEqual(out, [{ type: "error", stage: "x", message: "café" }]);
});

test("events are validated and normalised", () => {
  assert.equal(parseAssistEvent({ type: "nope" }), null);
  assert.equal(parseAssistEvent({ type: "customer", source: "guess", patch: {} }), null);
  assert.deepEqual(parseAssistEvent({ type: "history", status: "loading", lookup_key: "id:S1234567A" }), {
    type: "history",
    status: "loading",
    lookupKey: "id:S1234567A",
  });
  const history = parseAssistEvent({
    type: "history",
    status: "ok",
    lookup_key: "id:S1234567A",
    match_strategy: "nric_worker_permit_id",
    cases: [{ case_id: "CASE-1", status: "Open" }],
    open_count: 1,
    summary: "1 case",
  });
  assert.deepEqual(history, {
    type: "history",
    status: "ok",
    lookupKey: "id:S1234567A",
    matchedOn: "nric_worker_permit_id",
    cases: [{ case_id: "CASE-1", company: "", type: "", status: "Open", summary: "" }],
    openCount: 1,
    summary: "1 case",
  });
});

test("history results outside ok carry no cases or match", () => {
  const r = parseHistoryResult({
    status: "not_found",
    cases: [{ case_id: "X" }],
    match_strategy: "name",
    message: "None",
  });
  assert.deepEqual([r.status, r.cases, r.matchedOn, r.summary], ["not_found", [], null, "None"]);
  assert.equal(parseHistoryResult({ status: "weird" }).status, "error");
});
