import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  alignTurn,
  deriveLabelMap,
  initialAlignState,
  relabelDiarizedTurns,
  skippedLineIds,
  type AlignState,
  type ScriptLine,
  type ScriptRole,
} from "./script-align.ts";

const script = JSON.parse(
  readFileSync(new URL("../../../demo/scripts/sarah_lim_brightpath.json", import.meta.url), "utf8")
) as { lines: ScriptLine[] };
const lines = script.lines;
const idx = (id: string) => lines.findIndex((l) => l.id === id);

const noLabels = { speakerLabel: null, labelRole: () => null, prevRole: null };

function run(state: AlignState, text: string, extra: Partial<Parameters<typeof alignTurn>[2]> = {}) {
  return alignTurn(lines, state, { ...noLabels, ...extra, text });
}

function stateAt(lineId: string): AlignState {
  let state = initialAlignState();
  for (const line of lines.slice(0, idx(lineId))) state = run(state, line.text).state;
  return state;
}

test("every exact script line maps to its role and advances the cursor", () => {
  let state = initialAlignState();
  for (const line of lines) {
    const res = run(state, line.text);
    assert.equal(res.segments.length, 1, line.id);
    assert.equal(res.segments[0].lineId, line.id);
    assert.equal(res.segments[0].role, line.role);
    assert.equal(res.segments[0].confidence, "high");
    state = res.state;
  }
  assert.equal(state.cursor, lines.length);
});

test("STT-noised lines still match with high confidence", () => {
  const cases: Array<[string, string]> = [
    ["L02", "hi daniel my name is sara lim"],
    ["L04", "sure its s eight eight two three four five one d"],
    ["L08", "its about my employer again bright path logistics my september salary came in 450 short they called it an admin penalty but nobody told me"],
  ];
  for (const [lineId, heard] of cases) {
    const res = run(stateAt(lineId), heard);
    assert.equal(res.segments[0].lineId, lineId, heard);
    assert.equal(res.segments[0].confidence, "high", heard);
  }
});

test("a line split into two turns holds the cursor, then advances", () => {
  const before = stateAt("L10");
  const first = run(before, "The payslip just says admin penalty.");
  assert.equal(first.segments[0].lineId, "L10");
  assert.equal(first.segments[0].role, "customer");
  assert.equal(first.state.cursor, idx("L10"));

  const second = run(
    first.state,
    "And honestly, it started after I complained about my annual leave. My supervisor also cut my shifts from five days to three."
  );
  assert.equal(second.segments[0].lineId, "L10");
  assert.equal(second.state.cursor, idx("L11"));
});

test("a turn that merged two speakers is split into staff + customer", () => {
  const merged = `${lines[idx("L09")].text} ${lines[idx("L10")].text}`;
  const res = run(stateAt("L09"), merged);
  assert.equal(res.segments.length, 2);
  assert.deepEqual(
    res.segments.map((s) => [s.lineId, s.role]),
    [["L09", "staff"], ["L10", "customer"]]
  );
  assert.equal(res.segments[0].text, lines[idx("L09")].text);
  assert.equal(res.state.cursor, idx("L11"));
});

test("merged-turn split uses word-level speaker labels when present", () => {
  const staffWords = lines[idx("L09")].text.split(/\s+/);
  const custWords = lines[idx("L10")].text.split(/\s+/);
  const res = run(stateAt("L09"), [...staffWords, ...custWords].join(" "), {
    wordLabels: [...staffWords.map(() => "A"), ...custWords.map(() => "B")],
  });
  assert.deepEqual(res.segments.map((s) => s.speakerLabel), ["A", "B"]);
  assert.deepEqual(res.state.labelVotes, { A: { staff: 1, customer: 0 }, B: { staff: 0, customer: 1 } });
});

test("skipping lines jumps the cursor and reports the skipped lines", () => {
  const before = stateAt("L05");
  const res = run(before, lines[idx("L07")].text);
  assert.equal(res.segments[0].lineId, "L07");
  assert.equal(res.state.cursor, idx("L08"));
  assert.deepEqual(skippedLineIds(lines, res.state), ["L05", "L06"]);
});

test("ad-libs fall back to diarization without moving the cursor", () => {
  const before = stateAt("L05");
  const res = run(before, "Sorry, can you hold on for one second please", {
    speakerLabel: "B",
    labelRole: (l) => (l === "B" ? "customer" : null),
  });
  assert.equal(res.segments[0].source, "diarization");
  assert.equal(res.segments[0].role, "customer");
  assert.equal(res.state.cursor, before.cursor);
});

test("ad-libs with no diarization alternate from the previous role", () => {
  const res = run(stateAt("L05"), "Sorry, can you hold on for one second please", { prevRole: "customer" });
  assert.equal(res.segments[0].source, "alternation");
  assert.equal(res.segments[0].role, "staff");
});

test("low-confidence matches defer to disagreeing diarization", () => {
  const res = run(stateAt("L03"), "could you tell me your number again", {
    speakerLabel: "B",
    labelRole: () => "customer",
  });
  assert.notEqual(res.segments[0].confidence, "high");
  assert.equal(res.segments[0].role, "customer");
});

test("label votes flip an inverted A/B map only after a margin of 2", () => {
  const inverted: Record<string, ScriptRole> = { A: "customer", B: "staff" };
  assert.equal(deriveLabelMap({ A: { staff: 1, customer: 0 } }, inverted), null);
  assert.deepEqual(deriveLabelMap({ A: { staff: 2, customer: 0 } }, inverted), { A: "staff", B: "customer" });
  assert.deepEqual(deriveLabelMap({ B: { staff: 0, customer: 1 } }, {}), { B: "customer" });
});

test("relabel only touches diarization-sourced turns", () => {
  const turns = [
    { id: 1, role: "customer", roleSource: "diarization" as const, speakerLabel: "A" },
    { id: 2, role: "customer", roleSource: "manual" as const, speakerLabel: "A" },
    { id: 3, role: "customer", roleSource: "script" as const, speakerLabel: "A" },
  ];
  const out = relabelDiarizedTurns(turns, { A: "staff" });
  assert.deepEqual(out.map((t) => t.role), ["staff", "customer", "customer"]);
});
