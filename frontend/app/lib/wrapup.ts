// End-of-call wrap-up (POST /wrapup) and saving it as a case (POST /cases). See backend/wrapup.py.
import { trimmedCustomer } from "./customer-profile.ts";
import { obj, str } from "./parse.ts";
import { toAssistTurns } from "./transcript.ts";
import type { CustomerData, CustomerHistoryCase, Turn } from "./types.ts";

type WrapUpAction = { owner: "caller" | "centre"; action: string; due: string };

export type WrapUp = {
  summary: string;
  issue_type: string;
  case: { action: "new" | "update"; case_id: string | null; case_type: string; company: string; status: string };
  actions: WrapUpAction[];
  next_action: string;
  follow_up: { date: string; channel: string; reason: string };
  message_to_caller: { channel: "email" | "sms"; subject: string; body: string };
};

export type SaveResult = {
  status: "saved" | "disabled" | "invalid" | "error";
  caseId: string | null;
  action: "created" | "updated" | null;
  message: string;
};

export function buildWrapUpRequest(
  turns: Turn[],
  customer: CustomerData,
  history: { matchedOn: string | null; cases: CustomerHistoryCase[] }
) {
  return {
    turns: toAssistTurns(turns),
    customer: trimmedCustomer(customer),
    history: { match_strategy: history.matchedOn, cases: history.cases },
  };
}

/** The /wrapup response as a wrap-up, or null with the reason. */
export function parseWrapUpResponse(raw: unknown): { wrapup: WrapUp | null; message: string } {
  const body = obj(raw);
  const w = obj(body.wrapup);
  if (body.status !== "ok" || !str(w.summary)) {
    return { wrapup: null, message: str(body.message) || "The wrap-up could not be drafted. Please try again." };
  }
  const c = obj(w.case);
  const f = obj(w.follow_up);
  const m = obj(w.message_to_caller);
  const actions = Array.isArray(w.actions) ? w.actions.map(obj) : [];
  return {
    message: "",
    wrapup: {
      summary: str(w.summary),
      issue_type: str(w.issue_type),
      case: {
        action: c.action === "update" ? "update" : "new",
        case_id: c.action === "update" ? str(c.case_id) || null : null,
        case_type: str(c.case_type),
        company: str(c.company),
        status: str(c.status) || "Open",
      },
      actions: actions
        .filter((a) => str(a.action))
        .map((a) => ({ owner: a.owner === "caller" ? "caller" : "centre", action: str(a.action), due: str(a.due) })),
      next_action: str(w.next_action),
      follow_up: { date: str(f.date), channel: str(f.channel) || "phone", reason: str(f.reason) },
      message_to_caller: {
        channel: m.channel === "sms" ? "sms" : "email",
        subject: str(m.subject),
        body: str(m.body),
      },
    },
  };
}

export function buildSaveRequest(customer: CustomerData, wrapup: WrapUp) {
  return { customer: trimmedCustomer(customer), wrapup };
}

export function parseSaveResponse(raw: unknown): SaveResult {
  const body = obj(raw);
  const status = (["saved", "disabled", "invalid"] as const).find((s) => s === body.status) ?? "error";
  return {
    status,
    caseId: status === "saved" ? str(body.case_id) || null : null,
    action: body.action === "created" || body.action === "updated" ? body.action : null,
    message: str(body.message) || (status === "error" ? "The case could not be saved. Please try again." : ""),
  };
}
