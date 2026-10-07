import assert from "node:assert/strict";
import { test } from "node:test";

import { lineTimings, parseDemoParams, resolveScript, revealedText, scriptTurn, wordsOf } from "./demo-playback.ts";
import { KATHERINE_SCRIPT } from "./demo-script.ts";

test("demo params: off without ?demo, default script and speed, unknown script is off", () => {
  assert.equal(parseDemoParams(""), null);
  assert.equal(parseDemoParams("?foo=1"), null);
  assert.deepEqual(parseDemoParams("?demo"), { script: KATHERINE_SCRIPT, speed: 1 });
  assert.deepEqual(parseDemoParams("?demo=katherine&speed=1.5"), { script: KATHERINE_SCRIPT, speed: 1.5 });
  assert.equal(parseDemoParams("?demo&speed=abc")?.speed, 1);
  assert.equal(parseDemoParams("?demo&speed=-2")?.speed, 1);
  assert.equal(parseDemoParams("?demo=nobody"), null);
});

test("word reveal", () => {
  assert.deepEqual(wordsOf("  Hi   there, Bhavesh "), ["Hi", "there,", "Bhavesh"]);
  assert.equal(revealedText("Hi there, Bhavesh", 0), "");
  assert.equal(revealedText("Hi there, Bhavesh", 2), "Hi there,");
  assert.equal(revealedText("Hi there, Bhavesh", 9), "Hi there, Bhavesh");
});

test("speed scales every timing", () => {
  const base = lineTimings(1);
  const fast = lineTimings(2);
  assert.equal(fast.perWordMs, base.perWordMs / 2);
  assert.equal(fast.gapMs, base.gapMs / 2);
  assert.deepEqual(lineTimings(0), base);
});

test("script turns are labelled like diarised turns, with no turn-end wait", () => {
  const staff = scriptTurn({ role: "staff", text: "Hello" }, 1000);
  assert.equal(staff.speakerLabel, "A");
  assert.equal(staff.role, "staff");
  assert.equal(staff.roleSource, "diarization");
  assert.equal(staff.endedAt, 1000);
  assert.equal(staff.turnEndMs, 0);
  assert.equal(scriptTurn({ role: "customer", text: "Hi" }, 0).speakerLabel, "B");
});

test("the Katherine script alternates speakers, opens with Staff and gives the phone number as digits", () => {
  assert.equal(KATHERINE_SCRIPT[0].role, "staff");
  KATHERINE_SCRIPT.forEach((line, i) => assert.equal(line.role, i % 2 === 0 ? "staff" : "customer"));
  assert.ok(KATHERINE_SCRIPT.some((line) => line.text.includes("9123 4567")));
  assert.ok(KATHERINE_SCRIPT.some((line) => line.holdAfter));
});

test("{followUp} becomes the date a week from today", () => {
  const [line] = resolveScript(
    [{ role: "staff", text: "Call you on {followUp}, then {followUp}." }],
    new Date(2026, 9, 6)
  );
  assert.equal(line.text, "Call you on Tuesday 13 October, then Tuesday 13 October.");
  assert.ok(resolveScript(KATHERINE_SCRIPT, new Date()).every((l) => !l.text.includes("{")));
});
