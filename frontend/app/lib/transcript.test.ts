import assert from "node:assert/strict";
import { test } from "node:test";

import { resolveSpeakerRole, swapRoles, toAssistTurns } from "./transcript.ts";
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
