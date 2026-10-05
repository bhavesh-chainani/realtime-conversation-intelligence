// Shared UI types for the operator workspace.

export type SpeakerRole = "staff" | "customer" | "unknown";

/** How a turn's speaker was decided: voice diarization, or corrected by staff. */
export type RoleSource = "diarization" | "manual";

export type Turn = {
  id: string;
  text: string;
  speakerLabel: string | null;
  role: SpeakerRole;
  roleSource: RoleSource;
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
  details?: {
    possibleConversation?: string;
    priority?: string;
    [key: string]: unknown;
  };
};

export type SuggestionMeta = {
  origin: "live" | "fallback";
  latencyMs: number | null;
  llmMs?: number;
  model?: string;
};

export type CustomerData = {
  name: string;
  contact_number: string;
  email: string;
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
  "idle" | "loading" | "invalid_input" | "not_configured" | "not_found" | "ok" | "error";

export type HistoryMeta = {
  openCount: number;
  matchedOn: string | null;
};

const CLOSED_CASE_STATUSES = new Set(["resolved", "closed", "approved", "withdrawn", "completed"]);

/** Mirrors backend customer_history.is_open_case_status. */
export function isOpenCaseStatus(status: string): boolean {
  return !CLOSED_CASE_STATUSES.has(status.trim().toLowerCase());
}
