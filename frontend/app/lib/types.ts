// Shared UI types for the operator workspace.
import type { AlignConfidence, RoleSource, ScriptLine } from "./script-align.ts";

export type SpeakerRole = "staff" | "customer" | "unknown";

export type Turn = {
  id: string;
  text: string;
  speakerLabel: string | null;
  role: SpeakerRole;
  roleSource: RoleSource;
  scriptLineId?: string;
  alignScore?: number;
  alignConfidence?: AlignConfidence;
  turnOrder?: number;
  /** performance.now() when the turn was finalized; latency is measured from here. */
  committedAt: number;
};

export type Suggestion = {
  type?: string;
  text?: string;
  topic?: string;
  confidence?: number;
  linked_records?: string[];
  source?: string;
  details?: {
    possibleConversation?: string;
    operatorResponse?: string;
    suggestedConversation?: string;
    priority?: string;
    [key: string]: unknown;
  };
};

export type SuggestionMeta = {
  origin: "live" | "instant" | "fallback";
  latencyMs: number | null;
  llmMs?: number;
  model?: string;
  lineId?: string;
};

export type CustomerData = {
  name: string;
  nric_worker_permit_id: string;
  address: string;
  purpose_of_call: string;
};

export type CustomerDataField = keyof CustomerData;

/** Where a field's value came from: instant regex, LLM extraction, case records, or staff. */
export type FieldSource = "heard" | "ai" | "records" | "manual";

export type CustomerHistoryCase = {
  case_id: string;
  company: string;
  type: string;
  status: string;
  summary: string;
};

export type CustomerHistoryStatus =
  | "idle"
  | "loading"
  | "invalid_input"
  | "not_configured"
  | "not_found"
  | "ok"
  | "error";

export type HistoryMeta = {
  openCount: number;
  companies: string[];
  matchedOn: string | null;
};

export type DemoScenarioSummary = { id: string; title: string; description?: string };

export type DemoScenario = DemoScenarioSummary & {
  persona?: { name?: string; nric?: string; address?: string; employer?: string };
  staff_name?: string;
  autopilot?: { wpm?: number; gap_ms?: [number, number] };
  lines: ScriptLine[];
};

export type CacheStatus = {
  built: boolean;
  fresh: boolean;
  steps: number;
  total: number;
  built_at?: string;
  model?: string;
};

export type PreflightCheck = { ok: boolean; ms?: number; error?: string; [key: string]: unknown };

export type Preflight = {
  ok: boolean;
  llm: PreflightCheck;
  db: PreflightCheck;
  stt: PreflightCheck;
  cache: PreflightCheck;
  limits: PreflightCheck;
};

export type InputMode = "live" | "autopilot";
export type AutopilotState = "idle" | "running" | "waiting" | "paused" | "done";

const CLOSED_CASE_STATUSES = new Set(["resolved", "closed", "approved", "withdrawn", "completed"]);

/** Mirrors backend prompt_loader.is_open_case_status. */
export function isOpenCaseStatus(status: string): boolean {
  return !CLOSED_CASE_STATUSES.has(status.trim().toLowerCase());
}

/** A narrated milestone shown inline in the transcript (identity verified, case linked, …). */
export type MomentKind = "success" | "warning" | "info" | "link" | "wrapup";
export type Moment = { id: string; afterTurnId: string | null; kind: MomentKind; text: string };

export type Wrapup = {
  summary: string;
  issue?: string;
  linked_records?: string[];
  actions?: string[];
  documents_requested?: string[];
  follow_up?: string;
};

export type WrapupState = {
  status: "idle" | "loading" | "ready" | "error";
  data?: Wrapup;
  origin?: "live" | "prepared";
  latencyMs?: number;
};
