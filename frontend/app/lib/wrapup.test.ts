import assert from "node:assert/strict";
import { test } from "node:test";

import { EMPTY_CUSTOMER } from "./customer-profile.ts";
import { buildSaveRequest, buildWrapUpRequest, parseSaveResponse, parseWrapUpResponse } from "./wrapup.ts";

const RAW = {
  status: "ok",
  wrapup: {
    summary: "Two months unpaid.",
    issue_type: "Unpaid or late salary",
    case: {
      action: "new",
      case_id: "CASE-1",
      case_type: "Unpaid salary",
      company: "Lucky Star",
      status: "Claim to be filed",
    },
    actions: [
      { owner: "caller", action: "File the TADM claim", due: "2026-10-12" },
      { owner: "x", action: "" },
    ],
    next_action: "Caller to file the TADM claim",
    follow_up: { date: "2026-10-12", channel: "phone", reason: "Check it was filed." },
    message_to_caller: { channel: "email", subject: "Your call", body: "Hi Ahmad" },
  },
};

test("a wrap-up is parsed, dropping empty actions and the case ID of a new case", () => {
  const { wrapup, message } = parseWrapUpResponse(RAW);
  assert.equal(message, "");
  assert.equal(wrapup?.case.case_id, null);
  assert.deepEqual(wrapup?.actions, [{ owner: "caller", action: "File the TADM claim", due: "2026-10-12" }]);
  assert.equal(wrapup?.message_to_caller.channel, "email");
});

test("a failed wrap-up carries the backend's message", () => {
  assert.deepEqual(parseWrapUpResponse({ status: "error", message: "Not enough conversation." }), {
    wrapup: null,
    message: "Not enough conversation.",
  });
  assert.equal(parseWrapUpResponse(null).wrapup, null);
});

test("requests carry the trimmed caller card and the record", () => {
  const customer = { ...EMPTY_CUSTOMER, name: " Ahmad Rahim ", contact_number: "81112222" };
  const turns = [
    {
      id: "1",
      text: "Hi",
      speakerLabel: null,
      role: "customer" as const,
      roleSource: "manual" as const,
      committedAt: 0,
    },
  ];
  assert.deepEqual(buildWrapUpRequest(turns, customer, { matchedOn: "contact_number", cases: [] }), {
    turns: [{ role: "customer", text: "Hi" }],
    customer: { name: "Ahmad Rahim", contact_number: "81112222" },
    history: { match_strategy: "contact_number", cases: [] },
  });
  const wrapup = parseWrapUpResponse(RAW).wrapup!;
  assert.deepEqual(buildSaveRequest(customer, wrapup).customer, { name: "Ahmad Rahim", contact_number: "81112222" });
});

test("save responses", () => {
  assert.deepEqual(parseSaveResponse({ status: "saved", case_id: "CASE-2026-10422", action: "created" }), {
    status: "saved",
    caseId: "CASE-2026-10422",
    action: "created",
    message: "",
  });
  assert.equal(parseSaveResponse({ status: "disabled", message: "off" }).message, "off");
  assert.equal(parseSaveResponse("garbage").status, "error");
});
