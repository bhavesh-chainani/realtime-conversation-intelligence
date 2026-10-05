// Caller card, prior cases and the current suggestion, driven by /assist events and staff actions.
import type { AssistEvent, HistoryResult } from "./assist-stream.ts";
import {
  applyCustomerPatch,
  applyManualEdit,
  EMPTY_CUSTOMER,
  IDENTITY_FIELDS,
  trimmedCustomer,
  type FieldSources,
} from "./customer-profile.ts";
import { toAssistTurns } from "./transcript.ts";
import type {
  CustomerData,
  CustomerDataField,
  CustomerHistoryCase,
  CustomerHistoryStatus,
  Suggestion,
  SuggestionMeta,
  Turn,
} from "./types.ts";

/** Operators get one focused suggestion at a time. */
const MAX_SUGGESTIONS = 1;

type HistoryState = {
  status: CustomerHistoryStatus;
  summary: string;
  cases: CustomerHistoryCase[];
  /** Open cases in the record (0 unless matched). */
  openCount: number;
  lookupKey: string | null;
  matchedOn: string | null;
};

export type AssistState = {
  customer: CustomerData;
  sources: FieldSources;
  history: HistoryState;
  /** History status before the current "loading", restored if the request is abandoned. */
  statusBeforeLoading: CustomerHistoryStatus | null;
  suggestions: Suggestion[];
  suggestionMeta: SuggestionMeta | null;
  fetching: boolean;
  /** The live /assist stream; events from older streams are ignored. */
  gen: number;
  /** Bumped when staff change the identity or look up by hand; older lookup results are ignored. */
  epoch: number;
  streamEpoch: number;
  /** performance.now() when the turn behind the current stream was committed (suggestion latency). */
  committedAt: number;
  /** performance.now() when the caller stopped speaking (≤ committedAt). */
  endedAt: number;
};

export type AssistAction =
  | { type: "streamStart"; gen: number; committedAt: number; endedAt?: number }
  | { type: "event"; gen: number; event: AssistEvent; at: number }
  | { type: "streamEnd"; gen: number }
  | { type: "manualEdit"; field: CustomerDataField; value: string }
  | { type: "manualLookupStart" }
  | { type: "manualLookupResult"; epoch: number; result: HistoryResult; prefill: Record<string, string> | null }
  | { type: "reset" };

const IDLE_HISTORY: HistoryState = {
  status: "idle",
  summary: "",
  cases: [],
  openCount: 0,
  lookupKey: null,
  matchedOn: null,
};

export const initialAssistState: AssistState = {
  customer: EMPTY_CUSTOMER,
  sources: {},
  history: IDLE_HISTORY,
  statusBeforeLoading: null,
  suggestions: [],
  suggestionMeta: null,
  fetching: false,
  gen: 0,
  epoch: 0,
  streamEpoch: 0,
  committedAt: 0,
  endedAt: 0,
};

function historyFrom(result: HistoryResult): HistoryState {
  return {
    status: result.status,
    summary: result.summary,
    cases: result.cases,
    openCount: result.status === "ok" ? result.openCount : 0,
    lookupKey: result.lookupKey,
    matchedOn: result.matchedOn,
  };
}

function withPatch(state: AssistState, patch: Record<string, string>, source: "heard" | "ai" | "records"): AssistState {
  const next = applyCustomerPatch(state, patch, source);
  return next === state ? state : { ...state, customer: next.customer, sources: next.sources };
}

function applyEvent(state: AssistState, event: AssistEvent, at: number): AssistState {
  // Lookup results for an identity staff have since changed are out of date.
  const identityCurrent = state.streamEpoch === state.epoch;
  switch (event.type) {
    case "customer":
      if (event.source === "manual" || (event.source === "records" && !identityCurrent)) return state;
      return withPatch(state, event.patch, event.source);
    case "history":
      if (!identityCurrent) return state;
      if (event.status === "loading") {
        return {
          ...state,
          statusBeforeLoading: state.history.status === "loading" ? state.statusBeforeLoading : state.history.status,
          history: { ...state.history, status: "loading" },
        };
      }
      return { ...state, history: historyFrom(event), statusBeforeLoading: null };
    case "suggesting":
      return { ...state, fetching: true };
    case "suggestions": {
      const list = event.suggestions.slice(0, MAX_SUGGESTIONS);
      // An empty answer means the agent chose not to suggest: keep what is on screen.
      if (list.length === 0) return state;
      return {
        ...state,
        suggestions: list,
        suggestionMeta: {
          origin: event.fallback ? "fallback" : "live",
          latencyMs: at - state.endedAt,
          speakerMs: state.committedAt - state.endedAt,
          llmMs: event.fallback ? undefined : event.timings.llm_ms,
          model: event.fallback ? undefined : event.timings.model,
        },
      };
    }
    case "done":
      return { ...state, fetching: false };
    default:
      return state;
  }
}

export function assistReducer(state: AssistState, action: AssistAction): AssistState {
  switch (action.type) {
    case "streamStart":
      return {
        ...state,
        gen: action.gen,
        streamEpoch: state.epoch,
        committedAt: action.committedAt,
        endedAt: Math.min(action.endedAt ?? action.committedAt, action.committedAt),
      };
    case "event":
      return action.gen === state.gen ? applyEvent(state, action.event, action.at) : state;
    case "streamEnd": {
      if (action.gen !== state.gen) return state;
      const abandoned = state.history.status === "loading" && state.statusBeforeLoading !== null;
      return {
        ...state,
        fetching: false,
        history: abandoned ? { ...state.history, status: state.statusBeforeLoading! } : state.history,
        statusBeforeLoading: null,
      };
    }
    case "manualEdit": {
      const next = applyManualEdit(state, action.field, action.value);
      const identity = IDENTITY_FIELDS.includes(action.field);
      return {
        ...state,
        customer: next.customer,
        sources: next.sources,
        // A changed identity invalidates the record on screen and any lookup in flight.
        ...(identity ? { history: IDLE_HISTORY, statusBeforeLoading: null, epoch: state.epoch + 1 } : {}),
      };
    }
    case "manualLookupStart":
      return {
        ...state,
        epoch: state.epoch + 1,
        statusBeforeLoading: null,
        history: { ...state.history, status: "loading" },
      };
    case "manualLookupResult": {
      if (action.epoch !== state.epoch) return state;
      const next = { ...state, history: historyFrom(action.result) };
      return action.prefill ? withPatch(next, action.prefill, "records") : next;
    }
    case "reset":
      return { ...initialAssistState, gen: state.gen + 1, epoch: state.epoch + 1 };
  }
}

/** Identifies the customer record a suggestion was based on (mirrors backend profile.cases_key). */
export function casesKey(history: HistoryState): string {
  return `${history.matchedOn || "-"}|${history.cases.map((c) => c.case_id).join(",")}`;
}

export function buildAssistRequest(state: AssistState, turns: Turn[], extract: boolean) {
  return {
    turns: toAssistTurns(turns),
    customer: trimmedCustomer(state.customer),
    sources: state.sources,
    history: {
      lookup_key: state.history.lookupKey,
      match_strategy: state.history.matchedOn,
      cases: state.history.cases,
      status: state.history.status,
    },
    // What Staff see now, so the agent moves on once it has been asked rather than repeating it.
    previous_suggestions: state.suggestions
      .map((s) => s.details?.possibleConversation || s.topic || "")
      .filter((text) => text.trim()),
    extract,
    max_suggestions: MAX_SUGGESTIONS,
  };
}
