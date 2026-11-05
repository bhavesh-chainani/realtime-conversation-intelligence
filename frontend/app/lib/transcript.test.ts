import assert from "node:assert/strict";
import { test } from "node:test";

import { resolveSpeakerRole, resolveSpeakerRoles, sameAssistTurns, swapRoles, toAssistTurns } from "./transcript.ts";
import type { Turn } from "./types.ts";

test("first voice takes the next-voice role, the second the other, a third stays unknown", () => {
  const a = resolveSpeakerRole("A", {}, true);
  assert.deepEqual(a, { role: "staff", map: { A: "staff" } });
  const b = resolveSpeakerRole("B", a.map, true);
  assert.deepEqual(b, { role: "customer", map: { A: "staff", B: "customer" } });
  assert.equal(resolveSpeakerRole("C", b.map, true).role, "unknown");
  assert.equal(resolveSpeakerRole("A", {}, false).role, "customer");
  assert.equal(resolveSpeakerRole(null, b.map, true).role, "unknown");
});

test("swap and assist turns", () => {
  assert.deepEqual(swapRoles({ A: "staff", B: "customer" }), { A: "customer", B: "staff" });
  const turns = [
    { id: "1", text: "Hi", speakerLabel: "A", role: "staff", roleSource: "diarization", committedAt: 0 },
  ] satisfies Turn[];
  assert.deepEqual(toAssistTurns(turns), [{ role: "staff", text: "Hi" }]);
});

test("two new voices in one turn get different roles", () => {
  const { roles, map } = resolveSpeakerRoles(["A", "B", "A", null], {}, true);
  assert.deepEqual(roles, ["staff", "customer", "staff", "unknown"]);
  assert.deepEqual(map, { A: "staff", B: "customer" });
});

test("an early start fits only exactly the same turns", () => {
  const turns = [
    { role: "staff", text: "May I have your number?" },
    { role: "customer", text: "It's 9123 4567." },
  ] as const;
  assert.equal(sameAssistTurns([...turns], [...turns]), true);
  assert.equal(sameAssistTurns([...turns], [turns[0], { role: "staff", text: "It's 9123 4567." }]), false);
  assert.equal(sameAssistTurns([...turns], [turns[0], { role: "customer", text: "It's 9123." }]), false);
  assert.equal(sameAssistTurns([...turns], [...turns, { role: "customer", text: "Thanks." }]), false);
});
