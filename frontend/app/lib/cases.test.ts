import assert from "node:assert/strict";
import { test } from "node:test";

import { formatDueDate, isOpenCaseStatus, isOverdue } from "./cases.ts";

test("open statuses", () => {
  assert.ok(isOpenCaseStatus("Pending documents"));
  assert.ok(!isOpenCaseStatus(" Resolved "));
});

test("due dates", () => {
  assert.equal(formatDueDate("2026-10-12"), "Mon 12 Oct");
  assert.equal(formatDueDate("soon"), "soon");
  assert.ok(isOverdue("2026-10-03", "2026-10-05"));
  assert.ok(!isOverdue("2026-10-05", "2026-10-05"));
  assert.ok(!isOverdue("", "2026-10-05"));
});
