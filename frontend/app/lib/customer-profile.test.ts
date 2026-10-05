import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { applyCustomerPatch, applyManualEdit, EMPTY_CUSTOMER } from "./customer-profile.ts";
import type { FieldSource } from "./types.ts";

type PrecedenceCase = {
  case: string;
  values: Record<string, string>;
  sources: Record<string, FieldSource>;
  source: FieldSource;
  patch: Record<string, string>;
  accepted: Record<string, string>;
};

const fixture = new URL("../../../tests/fixtures/profile_precedence.json", import.meta.url);
const cases = JSON.parse(readFileSync(fixture, "utf8")) as PrecedenceCase[];

for (const c of cases) {
  test(`precedence (shared with backend): ${c.case}`, () => {
    const before = { customer: { ...EMPTY_CUSTOMER, ...c.values }, sources: { ...c.sources } };
    const after = applyCustomerPatch(before, c.patch, c.source);
    const accepted = Object.fromEntries(
      Object.entries(after.customer).filter(
        ([k, v]) =>
          v !== before.customer[k as keyof typeof before.customer] ||
          after.sources[k as keyof typeof after.sources] !== before.sources[k as keyof typeof before.sources]
      )
    );
    assert.deepEqual(accepted, c.accepted);
    if (Object.keys(c.accepted).length === 0) assert.equal(after, before);
  });
}

test("manual edits are kept and win over later patches", () => {
  const edited = applyManualEdit({ customer: EMPTY_CUSTOMER, sources: {} }, "name", "Kathy Liao");
  assert.equal(applyCustomerPatch(edited, { name: "Katherine Liao" }, "records"), edited);
});
