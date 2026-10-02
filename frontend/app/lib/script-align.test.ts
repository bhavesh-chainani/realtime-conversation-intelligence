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
    ["L02", "hi bavesh my name is sara lim and my nric is s one two three four five six seven a"],
    ["L04", "its my employer again bright path logistics they took 450 from my salary as an admin penalty right after i complained about my annual leave"],
    ["L06", "yes my supervisor sent me a whatsapp saying people who make trouble dont get full shifts"],
  ];
  for (const [lineId, heard] of cases) {
    const res = run(stateAt(lineId), heard);
    assert.equal(res.segments[0].lineId, lineId, heard);
    assert.equal(res.segments[0].confidence, "high", heard);
  }
});

test("a line split into two turns holds the cursor, then advances", () => {
  const before = stateAt("L04");
  const first = run(before, "It's my employer again, Brightpath Logistics.");
  assert.equal(first.segments[0].lineId, "L04");
  assert.equal(first.segments[0].role, "customer");
  assert.equal(first.state.cursor, idx("L04"));

  const second = run(
    first.state,
    "They took four hundred and fifty dollars from my salary as an admin penalty, right after I complained about my annual leave."
  );
  assert.equal(second.segments[0].lineId, "L04");
  assert.equal(second.state.cursor, idx("L05"));
});

test("a turn that merged two speakers is split into staff + customer", () => {
  const merged = `${lines[idx("L03")].text} ${lines[idx("L04")].text}`;
  const res = run(stateAt("L03"), merged);
  assert.equal(res.segments.length, 2);
  assert.deepEqual(
    res.segments.map((s) => [s.lineId, s.role]),
    [["L03", "staff"], ["L04", "customer"]]
  );
  assert.equal(res.segments[0].text, lines[idx("L03")].text);
  assert.equal(res.state.cursor, idx("L05"));
});

test("merged-turn split uses word-level speaker labels when present", () => {
  const staffWords = lines[idx("L03")].text.split(/\s+/);
  const custWords = lines[idx("L04")].text.split(/\s+/);
  const res = run(stateAt("L03"), [...staffWords, ...custWords].join(" "), {
    wordLabels: [...staffWords.map(() => "A"), ...custWords.map(() => "B")],
  });
  assert.deepEqual(res.segments.map((s) => s.speakerLabel), ["A", "B"]);
  assert.deepEqual(res.state.labelVotes, { A: { staff: 1, customer: 0 }, B: { staff: 0, customer: 1 } });
});

test("skipping lines jumps the cursor and reports the skipped lines", () => {
  const before = stateAt("L03");
  const res = run(before, lines[idx("L05")].text);
  assert.equal(res.segments[0].lineId, "L05");
  assert.equal(res.state.cursor, idx("L06"));
  assert.deepEqual(skippedLineIds(lines, res.state), ["L03", "L04"]);
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
  const res = run(stateAt("L05"), "do you have the letter with you", {
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
