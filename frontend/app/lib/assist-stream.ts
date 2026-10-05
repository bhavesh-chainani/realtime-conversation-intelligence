// Client for POST /assist: an NDJSON stream of events for one customer turn (see backend/orchestrator.py).
import type { CustomerHistoryCase, CustomerHistoryStatus, FieldSource, Suggestion } from "./types.ts";

export type HistoryResult = {
  status: CustomerHistoryStatus;
  lookupKey: string | null;
  matchedOn: string | null;
  cases: CustomerHistoryCase[];
  openCount: number;
  summary: string;
};

export type AssistEvent =
  | { type: "customer"; source: FieldSource; patch: Record<string, string> }
  | { type: "history"; status: "loading"; lookupKey: string | null }
  | ({ type: "history" } & HistoryResult)
  | { type: "suggesting"; round: number }
  | {
      type: "suggestions";
      round: number;
      suggestions: Suggestion[];
      fallback: boolean;
      timings: { llm_ms?: number; model?: string };
    }
  | { type: "error"; stage: string; message: string }
  | { type: "done" };

const HISTORY_STATUSES = new Set(["invalid_input", "not_configured", "not_found", "ok", "error"]);
const FIELD_SOURCES = new Set(["heard", "ai", "records", "manual"]);

/** Splits a text stream into JSON lines; tolerates lines split across chunks and skips bad lines. */
export class NdjsonDecoder {
  private buffer = "";

  push(chunk: string): unknown[] {
    this.buffer += chunk;
    const lines = this.buffer.split("\n");
    this.buffer = lines.pop() ?? "";
    return parseLines(lines);
  }

  flush(): unknown[] {
    const rest = this.buffer;
    this.buffer = "";
    return parseLines([rest]);
  }
}

function parseLines(lines: string[]): unknown[] {
  const out: unknown[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      out.push(JSON.parse(trimmed));
    } catch {
      console.warn("[assist] skipped malformed line", trimmed.slice(0, 80));
    }
  }
  return out;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** A /customer-history response or /assist history event, normalised for the caller card. */
export function parseHistoryResult(raw: Record<string, unknown>): HistoryResult {
  const status = (HISTORY_STATUSES.has(str(raw.status)) ? raw.status : "error") as CustomerHistoryStatus;
  const cases: CustomerHistoryCase[] =
    status === "ok" && Array.isArray(raw.cases)
      ? raw.cases.map((row) => {
          const c = (row ?? {}) as Record<string, unknown>;
          return {
            case_id: str(c.case_id),
            company: str(c.company),
            type: str(c.type),
            status: str(c.status),
            summary: str(c.summary),
          };
        })
      : [];
  return {
    status,
    lookupKey: str(raw.lookup_key) || null,
    matchedOn: status === "ok" ? str(raw.match_strategy) || null : null,
    cases,
    openCount: typeof raw.open_count === "number" ? raw.open_count : 0,
    summary: str(raw.summary) || str(raw.history_summary) || str(raw.message) || "No customer history found.",
  };
}

export function parseAssistEvent(raw: unknown): AssistEvent | null {
  if (!raw || typeof raw !== "object") return null;
  const ev = raw as Record<string, unknown>;
  switch (ev.type) {
    case "customer":
      if (!FIELD_SOURCES.has(str(ev.source)) || !ev.patch || typeof ev.patch !== "object") return null;
      return { type: "customer", source: ev.source as FieldSource, patch: ev.patch as Record<string, string> };
    case "history":
      if (ev.status === "loading") return { type: "history", status: "loading", lookupKey: str(ev.lookup_key) || null };
      return { type: "history", ...parseHistoryResult(ev) };
    case "suggesting":
      return { type: "suggesting", round: Number(ev.round) || 1 };
    case "suggestions":
      return {
        type: "suggestions",
        round: Number(ev.round) || 1,
        suggestions: Array.isArray(ev.suggestions) ? (ev.suggestions as Suggestion[]) : [],
        fallback: Boolean(ev.fallback),
        timings: (ev.timings ?? {}) as { llm_ms?: number; model?: string },
      };
    case "error":
      return { type: "error", stage: str(ev.stage), message: str(ev.message) };
    case "done":
      return { type: "done" };
    default:
      return null;
  }
}

/** POST /assist and call `onEvent` for each event until the stream ends or `signal` aborts. */
export async function streamAssist(
  backendUrl: string,
  body: unknown,
  signal: AbortSignal,
  onEvent: (event: AssistEvent) => void
): Promise<void> {
  const res = await fetch(`${backendUrl}/assist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`assist request failed: ${res.status}`);

  const reader = res.body.getReader();
  const text = new TextDecoder();
  const lines = new NdjsonDecoder();
  const emit = (raws: unknown[]) => {
    for (const raw of raws) {
      if (signal.aborted) return;
      const event = parseAssistEvent(raw);
      if (event) onEvent(event);
    }
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    emit(lines.push(text.decode(value, { stream: true })));
  }
  emit(lines.push(text.decode()));
  emit(lines.flush());
}
