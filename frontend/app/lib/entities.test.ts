import assert from "node:assert/strict";
import { test } from "node:test";

import { nextLookup } from "./lookup-guard.ts";
import { extractIntroName, extractNric } from "./quick-entities.ts";
import { areSimilar } from "./text-normalize.ts";

test("NRIC from written and spoken forms (mirrors backend tests)", () => {
  for (const text of [
    "Sure, it's S1234567A.",
    "Sure, it's S, one two three four five six seven, A.",
    "s 1234567 a",
    "my nric is s 1,234,567 a",
    "it's s 12 345 67 a",
  ]) {
    assert.equal(extractNric(text), "S1234567A", text);
  }
  assert.equal(extractNric("my permit is G 6690312 P"), "G6690312P");
  assert.equal(extractNric("G double six nine zero three one two P"), "G6690312P");
  assert.equal(extractNric("I have 3 kids and 12 days of leave"), null);
});

test("intro names (mirrors backend tests)", () => {
  assert.equal(extractIntroName("Hi Bhavesh, my name is Katherine Liao."), "Katherine Liao");
  assert.equal(extractIntroName("This is Rajesh Kumar calling"), "Rajesh Kumar");
  assert.equal(extractIntroName("I'm Maria Santos"), "Maria Santos");
  assert.equal(extractIntroName("I am Calling About my salary"), null);
  assert.equal(extractIntroName("my name is katherine"), null);
});

test("lookup guard: name first, ID supersedes, no repeats", () => {
  const byName = nextLookup(null, { name: "Katherine Liao", nric_worker_permit_id: "" });
  assert.deepEqual(byName, { key: "name:katherine liao", name: "Katherine Liao" });
  assert.equal(nextLookup(byName!.key, { name: "Katherine Liao", nric_worker_permit_id: "" }), null);

  const byId = nextLookup(byName!.key, { name: "Katherine Liao", nric_worker_permit_id: "s1234567a" });
  assert.deepEqual(byId, { key: "id:S1234567A", nric_worker_permit_id: "S1234567A" });
  assert.equal(nextLookup(byId!.key, { name: "Katherine Liao", nric_worker_permit_id: "S1234567A" }), null);
  // After an ID lookup, a name change alone does not trigger a name lookup.
  assert.equal(nextLookup(byId!.key, { name: "Katherine Tan", nric_worker_permit_id: "" }), null);
  // Single names and malformed IDs never trigger.
  assert.equal(nextLookup(null, { name: "Katherine", nric_worker_permit_id: "S88" }), null);
});

test("areSimilar treats formatted/unformatted variants as duplicates", () => {
  assert.ok(areSimilar("sure its s one two three four five six seven a", "Sure, it's S1234567A."));
  assert.ok(!areSimilar("My name is Katherine Liao.", "Can I confirm your address?"));
});
