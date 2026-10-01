import assert from "node:assert/strict";
import { test } from "node:test";

import { nextLookup } from "./lookup-guard.ts";
import { extractIntroName, extractNric } from "./quick-entities.ts";
import { areSimilar } from "./text-normalize.ts";

test("NRIC from written and spoken forms (mirrors backend tests)", () => {
  for (const text of [
    "Sure, it's S8823451D.",
    "Sure, it's S, eight eight two three four five one, D.",
    "s 8823451 d",
    "S double eight two three four five one D",
    "it's s 88 234 51 d",
  ]) {
    assert.equal(extractNric(text), "S8823451D", text);
  }
  assert.equal(extractNric("my permit is G 6690312 P"), "G6690312P");
  assert.equal(extractNric("I have 3 kids and 12 days of leave"), null);
});

test("intro names (mirrors backend tests)", () => {
  assert.equal(extractIntroName("Hi Daniel, my name is Sarah Lim."), "Sarah Lim");
  assert.equal(extractIntroName("This is Rajesh Kumar calling"), "Rajesh Kumar");
  assert.equal(extractIntroName("I'm Maria Santos"), "Maria Santos");
  assert.equal(extractIntroName("I am Calling About my salary"), null);
  assert.equal(extractIntroName("my name is sarah"), null);
});

test("lookup guard: name first, ID supersedes, no repeats", () => {
  const byName = nextLookup(null, { name: "Sarah Lim", nric_worker_permit_id: "" });
  assert.deepEqual(byName, { key: "name:sarah lim", name: "Sarah Lim" });
  assert.equal(nextLookup(byName!.key, { name: "Sarah Lim", nric_worker_permit_id: "" }), null);

  const byId = nextLookup(byName!.key, { name: "Sarah Lim", nric_worker_permit_id: "s8823451d" });
  assert.deepEqual(byId, { key: "id:S8823451D", nric_worker_permit_id: "S8823451D" });
  assert.equal(nextLookup(byId!.key, { name: "Sarah Lim", nric_worker_permit_id: "S8823451D" }), null);
  // After an ID lookup, a name change alone does not trigger a name lookup.
  assert.equal(nextLookup(byId!.key, { name: "Sarah Tan", nric_worker_permit_id: "" }), null);
  // Single names and malformed IDs never trigger.
  assert.equal(nextLookup(null, { name: "Sarah", nric_worker_permit_id: "S88" }), null);
});

test("areSimilar treats formatted/unformatted variants as duplicates", () => {
  assert.ok(areSimilar("sure its s eight eight two three four five one d", "Sure, it's S8823451D."));
  assert.ok(!areSimilar("My name is Sarah Lim.", "Can I confirm your address?"));
});
