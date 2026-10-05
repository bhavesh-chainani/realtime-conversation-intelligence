// Shared UI types for the operator workspace.

export type SpeakerRole = "staff" | "customer" | "unknown";

/** How a turn's speaker was decided: voice diarization, or corrected by staff. */
type RoleSource = "diarization" | "manual";

export type Turn = {
  id: string;
  text: string;
  speakerLabel: string | null;
  role: SpeakerRole;
  roleSource: RoleSource;
  /** performance.now() when the final (speaker-labelled) turn arrived. */
  committedAt: number;
  /** performance.now() when the caller stopped speaking, i.e. before the speaker wait; latency is measured from here. */
  endedAt?: number;
};

export type Suggestion = {
  type?: string;
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
  /** From the end of the customer's turn to the suggestion. */
  latencyMs: number;
  /** Part of latencyMs spent waiting for the diariser to confirm the speaker. */
  speakerMs: number;
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
  /** YYYY-MM-DD, or "" when unknown. */
  opened_on: string;
  next_action: string;
  follow_up_due: string;
};

export type CustomerHistoryStatus =
  "idle" | "loading" | "invalid_input" | "not_configured" | "not_found" | "ok" | "error";
