"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CustomerPanel } from "./components/customer-panel";
import { SessionHeader } from "./components/session-header";
import { SuggestionsPanel } from "./components/suggestions-panel";
import { TranscriptPanel } from "./components/transcript-panel";
import { nextLookup } from "./lib/lookup-guard.ts";
import { extractIntroName, extractNric } from "./lib/quick-entities.ts";
import {
  normalizeSpeakerLabel,
  parsePendingTurn,
  parseRelayInfo,
  parseRelaySegments,
  relayUrl,
  type PendingTurn,
  type RelaySegment,
} from "./lib/stt-relay.ts";
import { areSimilar } from "./lib/text-normalize.ts";
import {
  isOpenCaseStatus,
  type CustomerData,
  type CustomerDataField,
  type CustomerHistoryCase,
  type CustomerHistoryStatus,
  type FieldSource,
  type HistoryMeta,
  type Moment,
  type MomentKind,
  type SpeakerRole,
  type Suggestion,
  type SuggestionMeta,
  type Turn,
} from "./lib/types.ts";

type KnownRole = "staff" | "customer";
type SpeakerRoleMap = Record<string, KnownRole>;

type FinalTurnInput = {
  text: string;
  speakerLabel: string | null;
  wordLabels?: Array<string | null>;
  turnOrder?: number;
  /** Position within a relay turn split by speaker; each part is its own transcript turn. */
  segmentIndex?: number;
  /** Relay turns arrive after the next speaker's live text has started: leave it on screen. */
  keepLive?: boolean;
};

/** Customer-turn work waits this long so a split second fragment can merge first. */
const SUGGESTION_DEBOUNCE_MS = 250;
/** Max time a suggestion request waits for an in-flight history lookup. */
const LOOKUP_WAIT_MS = 700;
/** Streaming tokens are valid for 300 s; keep a spare one fresher than this for instant Start/Resume. */
const STT_TOKEN_MAX_AGE_MS = 240_000;
const TRANSCRIPT_WINDOW_TURNS = 16;
/** Operators get one focused suggestion at a time. */
const MAX_SUGGESTIONS = 1;

const EMPTY_CUSTOMER: CustomerData = {
  name: "",
  nric_worker_permit_id: "",
  address: "",
  purpose_of_call: "",
};

function newTurnId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `turn-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function extractSpeakerLabel(msg: Record<string, unknown>): string | null {
  const direct = normalizeSpeakerLabel(msg.speaker_label ?? msg.speaker);
  if (direct) return direct;

  const words = msg.words;
  if (!Array.isArray(words) || words.length === 0) return null;

  const counts = new Map<string, number>();
  for (const w of words) {
    if (!w || typeof w !== "object") continue;
    const word = w as Record<string, unknown>;
    const isFinal =
      word.word_is_final === true ||
      String(word.word_is_final).toLowerCase() === "true" ||
      word.end_of_word === true;
    if (!isFinal && word.word_is_final !== undefined) continue;
    const label = normalizeSpeakerLabel(word.speaker ?? word.speaker_label);
    if (!label) continue;
    counts.set(label, (counts.get(label) || 0) + 1);
  }

  let best: string | null = null;
  let bestCount = 0;
  for (const [label, count] of counts) {
    if (count > bestCount) {
      best = label;
      bestCount = count;
    }
  }
  return best;
}

/** Per-word diarization labels, only when they line up 1:1 with the transcript's words. */
function extractWordLabels(msg: Record<string, unknown>, text: string): Array<string | null> | undefined {
  const words = msg.words;
  if (!Array.isArray(words)) return undefined;
  const labels = words.map((w) =>
    w && typeof w === "object"
      ? normalizeSpeakerLabel((w as Record<string, unknown>).speaker ?? (w as Record<string, unknown>).speaker_label)
      : null
  );
  return labels.length === text.split(/\s+/).filter(Boolean).length ? labels : undefined;
}

function roleDisplayName(role: SpeakerRole): string {
  if (role === "staff") return "Staff";
  if (role === "customer") return "Customer";
  return "Unknown";
}

function formatLabeledTranscript(turns: Turn[]): string {
  return turns
    .map((t) => `${roleDisplayName(t.role)}: ${t.text}`)
    .join("\n")
    .trim();
}

function formatTranscriptWindow(turns: Turn[]): string {
  const recent = turns.slice(-TRANSCRIPT_WINDOW_TURNS);
  const omitted = turns.length - recent.length;
  const body = formatLabeledTranscript(recent);
  return omitted > 0 ? `[Earlier: ${omitted} turns omitted]\n${body}` : body;
}

function resolveSpeakerRole(
  label: string | null,
  map: SpeakerRoleMap,
  nextVoiceIsStaff: boolean
): { role: SpeakerRole; map: SpeakerRoleMap } {
  if (!label) return { role: "unknown", map };

  const existing = map[label];
  if (existing) return { role: existing, map };

  const assigned = new Set(Object.values(map));
  const firstRole: KnownRole = nextVoiceIsStaff ? "staff" : "customer";
  const secondRole: KnownRole = firstRole === "staff" ? "customer" : "staff";

  const nextMap = { ...map };
  if (!assigned.has(firstRole)) {
    nextMap[label] = firstRole;
    return { role: firstRole, map: nextMap };
  }
  if (!assigned.has(secondRole)) {
    nextMap[label] = secondRole;
    return { role: secondRole, map: nextMap };
  }
  return { role: "unknown", map };
}

function casesKey(match: string | null, cases: CustomerHistoryCase[]): string {
  return `${match || "-"}|${cases.map((c) => c.case_id).join(",")}`;
}

export default function Page() {
  const backendUrl =
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    (typeof window !== "undefined"
      ? localStorage.getItem("BACKEND_URL") || "http://localhost:8000"
      : "http://localhost:8000");

  const [turns, setTurns] = useState<Turn[]>([]);
  const [live, setLive] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const [liveRole, setLiveRole] = useState<SpeakerRole>("unknown");
  /** Finished turns the relay is still attributing to a speaker (Nemotron diarisation). */
  const [pendingTurns, setPendingTurns] = useState<PendingTurn[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [suggestionMeta, setSuggestionMeta] = useState<SuggestionMeta | null>(null);
  const [isFetchingSuggestions, setIsFetchingSuggestions] = useState(false);
  const [customerHistoryStatus, setCustomerHistoryStatus] =
    useState<CustomerHistoryStatus>("idle");
  const [customerHistory, setCustomerHistory] = useState("");
  const [customerHistoryCases, setCustomerHistoryCases] = useState<CustomerHistoryCase[]>([]);
  const [historyMeta, setHistoryMeta] = useState<HistoryMeta | null>(null);
  const [customerData, setCustomerData] = useState<CustomerData>(EMPTY_CUSTOMER);
  const [fieldSources, setFieldSources] = useState<Partial<Record<CustomerDataField, FieldSource>>>({});
  const [isListening, setIsListening] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [speakerRoleMap, setSpeakerRoleMap] = useState<SpeakerRoleMap>({});
  const [nextVoiceIsStaff, setNextVoiceIsStaff] = useState(true);

  // Call presentation
  const [moments, setMoments] = useState<Moment[]>([]);
  const [callStartedAt, setCallStartedAt] = useState<number | null>(null);
  const [callEndedAt, setCallEndedAt] = useState<number | null>(null);
  const [micLevel, setMicLevel] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const mediaRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const mediaSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const streamAttemptRef = useRef(0);
  const liveRef = useRef("");
  const speakerRoleMapRef = useRef<SpeakerRoleMap>({});
  const nextVoiceIsStaffRef = useRef(true);
  const transcriptListRef = useRef<HTMLDivElement>(null);

  // Conversation state mirrored in refs so async handlers never read stale values.
  const turnsRef = useRef<Turn[]>([]);
  /** State before the latest final, so a re-sent (formatted) version can replace it. */
  const lastFinalSnapshotRef = useRef<{ key: string; turns: Turn[] } | null>(null);
  const customerDataRef = useRef<CustomerData>(EMPTY_CUSTOMER);
  const fieldSourcesRef = useRef<Partial<Record<CustomerDataField, FieldSource>>>({});
  const historyCasesRef = useRef<CustomerHistoryCase[]>([]);
  const historyMatchRef = useRef<string | null>(null);
  const lookupKeyRef = useRef<string | null>(null);
  const lookupPromiseRef = useRef<Promise<void> | null>(null);
  const lookupReqIdRef = useRef(0);
  const lastCustomerTurnRef = useRef<Turn | null>(null);
  const lastSuggestionCasesKeyRef = useRef("");
  const suggestTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const suggestReqIdRef = useRef(0);
  const suggestAbortRef = useRef<AbortController | null>(null);
  const extractReqIdRef = useRef(0);
  const lastCustomerDataExtractRef = useRef("");

  const momentKeysRef = useRef<Set<string>>(new Set());
  const micTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const callEndedRef = useRef(false);
  const lastPartialLabelRef = useRef<string | null>(null);
  const pendingTurnsRef = useRef<PendingTurn[]>([]);
  const sttDropCountRef = useRef(0);
  const sttTokenRef = useRef<{ payload: Record<string, unknown>; fetchedAt: number } | null>(null);

  useEffect(() => {
    const el = transcriptListRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [turns, live, pendingTurns]);

  const transcriptText = useMemo(() => formatLabeledTranscript(turns), [turns]);
  const hasTranscript = transcriptText.trim().length >= 10;

  // ---------------------------------------------------------------------------
  // State helpers (keep refs and React state in step)
  // ---------------------------------------------------------------------------

  const commitTurns = (next: Turn[]) => {
    turnsRef.current = next;
    setTurns(next);
  };

  const setRoleMap = (map: SpeakerRoleMap) => {
    speakerRoleMapRef.current = map;
    setSpeakerRoleMap(map);
  };

  /** Merge values into the customer profile, respecting staff edits and verified records. */
  const applyCustomerPatch = (patch: Partial<CustomerData>, source: FieldSource) => {
    const data = { ...customerDataRef.current };
    const sources = { ...fieldSourcesRef.current };
    let changed = false;
    (Object.keys(patch) as CustomerDataField[]).forEach((field) => {
      const value = (patch[field] || "").trim();
      if (!value) return;
      const current = sources[field];
      if (current === "manual") return;
      if (current === "records" && source !== "records") return;
      // The instant regex hears the ID exactly; don't let LLM reformatting replace it.
      if (source === "ai" && field === "nric_worker_permit_id" && current === "heard") return;
      if (data[field] === value && current === source) return;
      data[field] = value;
      sources[field] = source;
      changed = true;
    });
    if (!changed) return;
    customerDataRef.current = data;
    fieldSourcesRef.current = sources;
    setCustomerData(data);
    setFieldSources(sources);
  };

  const resetHistory = () => {
    lookupReqIdRef.current += 1;
    historyCasesRef.current = [];
    historyMatchRef.current = null;
    setCustomerHistoryStatus("idle");
    setCustomerHistory("");
    setCustomerHistoryCases([]);
    setHistoryMeta(null);
  };

  const profilePayload = (): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const [key, value] of Object.entries(customerDataRef.current)) {
      if (value.trim()) out[key] = value.trim();
    }
    if (historyMatchRef.current) out.record_match = historyMatchRef.current;
    return out;
  };

  /** Narrate a milestone once, anchored after a transcript turn (default: the latest). */
  const emitMoment = (key: string, kind: MomentKind, text: string, afterTurnId?: string | null) => {
    if (momentKeysRef.current.has(key)) return;
    momentKeysRef.current.add(key);
    const anchor =
      afterTurnId !== undefined ? afterTurnId : turnsRef.current[turnsRef.current.length - 1]?.id ?? null;
    setMoments((prev) => [...prev, { id: key, afterTurnId: anchor, kind, text }]);
  };

  /** First time a suggestion cites a case, say so in the conversation. */
  const noteCitations = (list: Suggestion[], turnId: string) => {
    const byId = new Map(historyCasesRef.current.map((c) => [c.case_id, c]));
    for (const s of list) {
      for (const id of s.linked_records || []) {
        const c = byId.get(id);
        if (!c) continue;
        emitMoment(
          `cite:${id}`,
          "link",
          isOpenCaseStatus(c.status) ? `Linked to open case ${id}` : `Referenced past case ${id} · ${c.status}`,
          turnId
        );
      }
    }
  };

  // ---------------------------------------------------------------------------
  // Suggestions
  // ---------------------------------------------------------------------------

  const fetchSuggestions = async (turn: Turn) => {
    const context = formatTranscriptWindow(turnsRef.current);
    if (context.trim().length < 10) return;

    const reqId = ++suggestReqIdRef.current;
    suggestAbortRef.current?.abort();
    const controller = new AbortController();
    suggestAbortRef.current = controller;

    const cases = historyCasesRef.current;
    const profile = profilePayload();
    lastSuggestionCasesKeyRef.current = casesKey(historyMatchRef.current, cases);

    setIsFetchingSuggestions(true);
    try {
      const payload = {
        context,
        max_suggestions: MAX_SUGGESTIONS,
        customer_profile: Object.keys(profile).length ? profile : undefined,
        customer_history: cases.length ? cases : undefined,
      };

      const res = await fetch(`${backendUrl}/suggest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`Suggestion request failed: ${res.status}`);
      const data = (await res.json()) as Record<string, unknown>;
      if (reqId !== suggestReqIdRef.current) return;

      const list = Array.isArray(data.suggestions)
        ? (data.suggestions as Suggestion[]).slice(0, MAX_SUGGESTIONS)
        : [];
      const timings = (data.timings || {}) as { llm_ms?: number; model?: string };
      if (data.fallback) {
        if (list.length) {
          setSuggestions(list);
          setSuggestionMeta({ origin: "fallback", latencyMs: performance.now() - turn.committedAt });
        }
        return;
      }
      // The agent chose not to suggest: keep whatever is on screen.
      if (list.length === 0) return;
      setSuggestions(list);
      setSuggestionMeta({
        origin: "live",
        latencyMs: performance.now() - turn.committedAt,
        llmMs: timings.llm_ms,
        model: timings.model,
      });
      noteCitations(list, turn.id);
    } catch (err) {
      if (controller.signal.aborted) return;
      console.error("[Frontend] Failed to fetch suggestions:", err);
    } finally {
      if (reqId === suggestReqIdRef.current) setIsFetchingSuggestions(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Customer data: LLM extraction + history lookup
  // ---------------------------------------------------------------------------

  const runHistoryLookup = async (args: { name?: string; nric_worker_permit_id?: string }) => {
    const reqId = ++lookupReqIdRef.current;
    setCustomerHistoryStatus("loading");

    try {
      const res = await fetch(`${backendUrl}/customer-history`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: args.name || undefined,
          nric_worker_permit_id: args.nric_worker_permit_id || undefined,
          }),
      });
      const body = await res.json();
      if (reqId !== lookupReqIdRef.current) return;
      if (!res.ok || !body || typeof body !== "object") {
        setCustomerHistoryStatus("error");
        setCustomerHistory("Unable to obtain customer history at the moment.");
        return;
      }

      const status = typeof body.status === "string" ? body.status : "error";
      const summary = typeof body.history_summary === "string" ? body.history_summary : "";
      const message = typeof body.message === "string" ? body.message : "";
      const normalizedStatus: CustomerHistoryStatus =
        status === "invalid_input" ||
        status === "not_configured" ||
        status === "not_found" ||
        status === "ok" ||
        status === "error"
          ? status
          : "error";
      const cases: CustomerHistoryCase[] =
        normalizedStatus === "ok" && Array.isArray(body.cases)
          ? body.cases.map((row: any) => ({
              case_id: String(row.case_id || ""),
              company: String(row.company || ""),
              type: String(row.type || ""),
              status: String(row.status || ""),
              summary: String(row.summary || ""),
            }))
          : [];
      const matchedOn =
        normalizedStatus === "ok" && typeof body.match_strategy === "string" ? body.match_strategy : null;

      const openCount =
        typeof body.open_count === "number" ? body.open_count : cases.filter((c) => isOpenCaseStatus(c.status)).length;
      historyCasesRef.current = cases;
      historyMatchRef.current = matchedOn;
      setCustomerHistoryStatus(normalizedStatus);
      setCustomerHistory(summary || message || "No customer history found.");
      setCustomerHistoryCases(cases);

      const anchor = lastCustomerTurnRef.current?.id ?? null;
      if (matchedOn === "name") {
        emitMoment("match:name", "warning", "Possible match in records · verify NRIC", anchor);
      } else if (matchedOn === "nric_worker_permit_id") {
        emitMoment("match:verified", "success", "Identity verified · NRIC matches records", anchor);
        emitMoment(
          "returning",
          "info",
          `Returning customer · ${cases.length} prior case${cases.length === 1 ? "" : "s"}, ${openCount} open`,
          anchor
        );
      } else if (normalizedStatus === "not_found" && args.nric_worker_permit_id) {
        emitMoment("new-customer", "info", "New customer · no prior cases", anchor);
      }

      setHistoryMeta(
        normalizedStatus === "ok"
          ? {
              openCount,
              matchedOn,
            }
          : null
      );

      // Only an NRIC match is a verified identity, so only then prefill from records.
      if (matchedOn === "nric_worker_permit_id" && body.customer && typeof body.customer === "object") {
        const c = body.customer as Record<string, unknown>;
        applyCustomerPatch(
          {
            name: typeof c.name === "string" ? c.name : "",
            nric_worker_permit_id: typeof c.nric_worker_permit_id === "string" ? c.nric_worker_permit_id : "",
            address: typeof c.address === "string" ? c.address : "",
          },
          "records"
        );
      }

      // Records arrived after the last suggestion was requested: refresh it with the new context.
      const lastTurn = lastCustomerTurnRef.current;
      if (
        lastTurn &&
        !callEndedRef.current &&
        !suggestTimerRef.current &&
        casesKey(matchedOn, cases) !== lastSuggestionCasesKeyRef.current
      ) {
        void fetchSuggestions(lastTurn);
      }
    } catch (err) {
      if (reqId !== lookupReqIdRef.current) return;
      console.error("[Frontend] Failed to obtain customer history:", err);
      setCustomerHistoryStatus("error");
      setCustomerHistory("Unable to obtain customer history at the moment.");
    }
  };

  const maybeAutoLookup = () => {
    const req = nextLookup(lookupKeyRef.current, customerDataRef.current);
    if (!req) return;
    lookupKeyRef.current = req.key;
    const promise = runHistoryLookup({ name: req.name, nric_worker_permit_id: req.nric_worker_permit_id });
    lookupPromiseRef.current = promise;
    void promise.finally(() => {
      if (lookupPromiseRef.current === promise) lookupPromiseRef.current = null;
    });
  };

  const extractCustomerData = async () => {
    const context = formatLabeledTranscript(turnsRef.current);
    if (context.trim().length < 10) return;
    if (context.trim().toLowerCase() === lastCustomerDataExtractRef.current.trim().toLowerCase()) return;
    lastCustomerDataExtractRef.current = context;
    const reqId = ++extractReqIdRef.current;

    try {
      const res = await fetch(`${backendUrl}/extract-customer-data`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_transcript: context }),
      });
      if (!res.ok) return;
      const data = (await res.json()) as Record<string, unknown>;
      if (reqId !== extractReqIdRef.current) return;

      const rawData =
        data.data && typeof data.data === "object" ? (data.data as Record<string, unknown>) : null;
      if (Boolean(data.success) && rawData) {
        const patch: Partial<CustomerData> = {};
        (Object.keys(EMPTY_CUSTOMER) as CustomerDataField[]).forEach((field) => {
          const v = rawData[field];
          if (typeof v === "string" && v.trim()) patch[field] = v;
        });
        applyCustomerPatch(patch, "ai");
        maybeAutoLookup();
      }
    } catch (err) {
      console.error("[Frontend] Failed to extract customer data:", err);
    }
  };

  // ---------------------------------------------------------------------------
  // Turn ingestion
  // ---------------------------------------------------------------------------

  /** Everything that should happen when the customer finishes speaking. */
  const handleCustomerTurn = (turn: Turn) => {
    lastCustomerTurnRef.current = turn;

    // Instant identity capture: fills fields and starts the DB lookup before any LLM returns.
    const patch: Partial<CustomerData> = {};
    const nric = extractNric(turn.text);
    const name = extractIntroName(turn.text);
    if (nric) patch.nric_worker_permit_id = nric;
    if (name) patch.name = name;
    if (nric || name) applyCustomerPatch(patch, "heard");
    maybeAutoLookup();

    if (suggestTimerRef.current) clearTimeout(suggestTimerRef.current);
    suggestTimerRef.current = setTimeout(async () => {
      suggestTimerRef.current = null;
      const data = customerDataRef.current;
      if (!data.name || !data.nric_worker_permit_id || !data.address || !data.purpose_of_call) {
        void extractCustomerData();
      }
      const pendingLookup = lookupPromiseRef.current;
      if (pendingLookup) {
        await Promise.race([pendingLookup, new Promise((r) => setTimeout(r, LOOKUP_WAIT_MS))]);
      }
      const latest = lastCustomerTurnRef.current;
      if (latest) void fetchSuggestions(latest);
    }, SUGGESTION_DEBOUNCE_MS);
  };

  const labelRoleFor = (label: string | null): KnownRole | null => {
    if (!label) return null;
    const mapped = speakerRoleMapRef.current[label];
    if (mapped) return mapped;
    const resolved = resolveSpeakerRole(label, speakerRoleMapRef.current, nextVoiceIsStaffRef.current);
    if (resolved.map !== speakerRoleMapRef.current) setRoleMap(resolved.map);
    return resolved.role === "unknown" ? null : resolved.role;
  };

  const ingestPartial = (text: string, speakerLabel: string | null) => {
    const trimmed = text.trim();
    setLive(trimmed);
    liveRef.current = trimmed;
    lastPartialLabelRef.current = speakerLabel;
    setLiveRole(labelRoleFor(speakerLabel) ?? "unknown");
  };

  const ingestSpeechStart = () => {
    setSpeaking(true);
    if (!liveRef.current) setLiveRole("unknown");
  };

  const setPending = (next: PendingTurn[]) => {
    pendingTurnsRef.current = next;
    setPendingTurns(next);
  };

  /** The relay finished a turn and is identifying its speakers: move its text out of the live line. */
  const ingestPendingTurn = (pending: PendingTurn) => {
    setSpeaking(false);
    setLive("");
    liveRef.current = "";
    setLiveRole("unknown");
    if (pending.text) setPending([...pendingTurnsRef.current, pending]);
  };

  /** A relay turn arrived with its speakers: commit one transcript turn per speaker. */
  const ingestRelayTurn = (turnOrder: number | undefined, segments: RelaySegment[]) => {
    setPending(pendingTurnsRef.current.filter((p) => p.turnOrder !== turnOrder));
    segments.forEach((seg, segmentIndex) =>
      ingestFinalTurn({ ...seg, turnOrder, segmentIndex, keepLive: true })
    );
  };

  const ingestFinalTurn = (input: FinalTurnInput) => {
    const text = input.text.trim();
    if (!input.keepLive) {
      setSpeaking(false);
      setLive("");
      liveRef.current = "";
      setLiveRole("unknown");
    }
    if (!text) return;

    // A re-sent version of the latest turn (formatted text, same turn_order) replaces it.
    let baseTurns = turnsRef.current;
    const last = baseTurns[baseTurns.length - 1];
    const snapshot = lastFinalSnapshotRef.current;
    const orderKey =
      input.turnOrder !== undefined ? `order:${input.turnOrder}:${input.segmentIndex ?? 0}` : undefined;
    const isResend =
      !!snapshot &&
      !!last &&
      (orderKey !== undefined ? snapshot.key === orderKey : last.roleSource !== "manual" && areSimilar(text, last.text));
    if (isResend && snapshot) {
      baseTurns = snapshot.turns;
    } else {
      lastFinalSnapshotRef.current = { key: orderKey ?? `turn:${baseTurns.length}`, turns: baseTurns };
    }

    const turn: Turn = {
      id: newTurnId(),
      text,
      speakerLabel: input.speakerLabel,
      role: labelRoleFor(input.speakerLabel) ?? "unknown",
      roleSource: "diarization",
      turnOrder: input.turnOrder,
      committedAt: performance.now(),
    };
    commitTurns([...baseTurns, turn]);

    // Unlabelled turns may be the customer too, so they also trigger guidance.
    if (turn.role !== "staff") handleCustomerTurn(turn);
  };

  // Long-lived callbacks (WebSocket) call through this ref to the latest closures.
  /** The STT socket dropped or never connected: say so, instead of silently going quiet. */
  const notifySttDrop = () => {
    sttDropCountRef.current += 1;
    emitMoment(`stt-drop:${sttDropCountRef.current}`, "warning", "Transcription disconnected · press Resume");
    closeWs();
  };

  const handlers = { ingestFinalTurn, ingestPartial, ingestSpeechStart, ingestPendingTurn, ingestRelayTurn, notifySttDrop };
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  // ---------------------------------------------------------------------------
  // Speaker controls
  // ---------------------------------------------------------------------------

  const swapSpeakerRoles = useCallback(() => {
    const next: SpeakerRoleMap = {};
    for (const [label, role] of Object.entries(speakerRoleMapRef.current)) {
      next[label] = role === "staff" ? "customer" : "staff";
    }
    setRoleMap(next);
    commitTurns(
      turnsRef.current.map((t) => ({
        ...t,
        role: t.role === "staff" ? "customer" : t.role === "customer" ? "staff" : t.role,
      }))
    );
    setLiveRole((prev) =>
      prev === "staff" ? "customer" : prev === "customer" ? "staff" : prev
    );
    setNextVoiceIsStaff((prev) => {
      const next = !prev;
      nextVoiceIsStaffRef.current = next;
      return next;
    });
  }, []);

  const setNextVoiceRole = useCallback((staff: boolean) => {
    nextVoiceIsStaffRef.current = staff;
    setNextVoiceIsStaff(staff);
  }, []);

  /** Staff correction of one turn. */
  const flipTurn = (turnId: string) => {
    const target = turnsRef.current.find((t) => t.id === turnId);
    if (!target) return;
    const role: KnownRole = target.role === "staff" ? "customer" : "staff";
    const updated: Turn = { ...target, role, roleSource: "manual" };
    commitTurns(turnsRef.current.map((t) => (t.id === turnId ? updated : t)));
    if (role === "customer" && turnsRef.current[turnsRef.current.length - 1]?.id === turnId) {
      handleCustomerTurn(updated);
    }
  };

  // ---------------------------------------------------------------------------
  // Microphone streaming (AssemblyAI v3)
  // ---------------------------------------------------------------------------

  function closeWs() {
    streamAttemptRef.current += 1;
    if (micTimerRef.current) clearInterval(micTimerRef.current);
    micTimerRef.current = null;
    setMicLevel(0);

    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) {
      try {
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
      } catch {}
      try {
        ws.close();
      } catch {}
    }

    const proc = processorRef.current;
    processorRef.current = null;
    if (proc) {
      try {
        proc.onaudioprocess = null;
      } catch {}
      try {
        proc.disconnect();
      } catch {}
    }

    try {
      mediaSourceRef.current?.disconnect();
    } catch {}
    mediaSourceRef.current = null;

    const ctx = audioCtxRef.current;
    audioCtxRef.current = null;
    try {
      ctx?.close();
    } catch {}

    try {
      mediaRef.current?.getTracks().forEach((track) => track.stop());
    } catch {}
    mediaRef.current = null;

    setLive("");
    liveRef.current = "";
    setPending([]);
    setSpeaking(false);
    setLiveRole("unknown");
    setIsListening(false);
    setIsConnecting(false);
  }

  async function fetchSttToken(): Promise<Record<string, unknown>> {
    const res = await fetch(`${backendUrl}/assemblyai-token`);
    if (!res.ok) throw new Error(`token status ${res.status}`);
    return (await res.json()) as Record<string, unknown>;
  }

  /** Keep a spare single-use token ready so Start/Resume skips a ~1 s round trip. */
  async function prefetchSttToken() {
    const cached = sttTokenRef.current;
    if (cached && Date.now() - cached.fetchedAt < STT_TOKEN_MAX_AGE_MS) return;
    try {
      const payload = await fetchSttToken();
      sttTokenRef.current = { payload, fetchedAt: Date.now() };
    } catch {
      sttTokenRef.current = null;
    }
  }

  async function takeSttToken(): Promise<Record<string, unknown>> {
    const cached = sttTokenRef.current;
    sttTokenRef.current = null;
    if (cached && Date.now() - cached.fetchedAt < STT_TOKEN_MAX_AGE_MS) {
      return cached.payload;
    }
    return fetchSttToken();
  }

  async function openWs() {
    closeWs();
    startCall();
    setIsConnecting(true);
    const myAttempt = streamAttemptRef.current;
    // Token and microphone in parallel: the token round trip used to add ~1 s before listening.
    const tokenPromise = takeSttToken();
    tokenPromise.catch(() => {});

    // A new stream may assign A/B differently: forget the old label map.
    setRoleMap({});
    nextVoiceIsStaffRef.current = true;
    setNextVoiceIsStaff(true);
    setLiveRole("unknown");
    lastFinalSnapshotRef.current = null;

    const media = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (myAttempt !== streamAttemptRef.current) {
      media.getTracks().forEach((track) => track.stop());
      return;
    }
    mediaRef.current = media;

    const AudioContextCls =
      (window as any).AudioContext || (window as any).webkitAudioContext;
    const ctx = new AudioContextCls();
    audioCtxRef.current = ctx;
    const source = ctx.createMediaStreamSource(media);
    mediaSourceRef.current = source;
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    processorRef.current = proc;
    source.connect(proc);
    proc.connect(ctx.destination);

    // Mic level for the header's listening indicator (~10 Hz).
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);
    micTimerRef.current = setInterval(() => {
      analyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (const v of samples) {
        const x = (v - 128) / 128;
        sum += x * x;
      }
      setMicLevel(Math.min(1, Math.sqrt(sum / samples.length) * 5));
    }, 100);

    function pcmEncode(input: Float32Array) {
      const out = new Int16Array(input.length);
      for (let i = 0; i < input.length; i += 1) {
        out[i] = Math.max(-1, Math.min(1, input[i])) * 0x7fff;
      }
      return out.buffer;
    }

    let wsUrl = "";
    let streamingToken = "";
    let keytermsPrompt: string[] = [];
    let sttPrompt = "";
    let speechModel = "";
    let streamParams: Record<string, string> = {};
    try {
      const payload = await tokenPromise;
      if (myAttempt !== streamAttemptRef.current) return;
      // Nemotron diarisation on: audio goes through the backend relay instead of straight to AssemblyAI.
      const relay = parseRelayInfo(payload);
      if (relay) wsUrl = relayUrl(backendUrl, relay, ctx.sampleRate || 48000);
      streamingToken = String(payload.token || "").trim();
      keytermsPrompt = Array.isArray(payload.keyterms_prompt)
        ? payload.keyterms_prompt.map((t: unknown) => String(t))
        : [];
      sttPrompt = typeof payload.prompt === "string" ? payload.prompt : "";
      speechModel = typeof payload.speech_model === "string" ? payload.speech_model : "";
      if (payload.stream_params && typeof payload.stream_params === "object") {
        streamParams = Object.fromEntries(
          Object.entries(payload.stream_params as Record<string, unknown>).map(([k, v]) => [k, String(v)])
        );
      }
      if (!wsUrl && !streamingToken) {
        alert("Backend returned empty streaming token.");
        closeWs();
        return;
      }
    } catch (err) {
      console.error("[Frontend] Failed to obtain streaming token:", err);
      alert("Unable to obtain streaming token. Check backend.");
      closeWs();
      return;
    }

    if (!wsUrl) {
      const params = new URLSearchParams({
        sample_rate: String(ctx.sampleRate || 48000),
        format_turns: "true",
        speaker_labels: "true",
        max_speakers: "2",
        token: streamingToken,
      });
      if (keytermsPrompt.length > 0) {
        params.set("keyterms_prompt", JSON.stringify(keytermsPrompt));
      }
      if (sttPrompt) params.set("prompt", sttPrompt);
      if (speechModel) params.set("speech_model", speechModel);
      for (const [key, value] of Object.entries(streamParams)) params.set(key, value);
      wsUrl = `wss://streaming.assemblyai.com/v3/ws?${params}`;
    }

    const ws = new WebSocket(wsUrl);
    if (myAttempt !== streamAttemptRef.current) {
      try {
        ws.close();
      } catch {}
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnecting(false);
      setIsListening(true);
      void prefetchSttToken(); // ready for the next Resume
    };
    // closeWs() detaches this handler first, so reaching it means the connection was lost.
    ws.onclose = (evt) => {
      console.warn("[STT] Closed", evt.code, evt.reason);
      setIsListening(false);
      if (wsRef.current === ws) handlersRef.current.notifySttDrop();
    };
    ws.onerror = () => setIsListening(false);

    proc.onaudioprocess = (e: AudioProcessingEvent) => {
      const socket = wsRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      const pcm = pcmEncode(e.inputBuffer.getChannelData(0));
      try {
        socket.send(pcm);
      } catch {}
    };

    ws.onmessage = (evt) => {
      try {
        const d = JSON.parse(evt.data as string) as Record<string, unknown>;
        if (d.type === "SpeechStarted") {
          // u3 models send few partials: show "speaking" straight away so the screen never looks frozen.
          handlersRef.current.ingestSpeechStart();
          return;
        }
        if (d.type === "Begin") {
          console.info("[STT] Session started", d);
          return;
        }
        if (d.type === "Error" || d.error) {
          // Fail loudly: a silent STT failure looks like a frozen call.
          console.error("[STT] Error", d);
          alert(`Transcription error: ${String(d.error || "unknown")}`);
          closeWs();
          return;
        }
        const pending = parsePendingTurn(d);
        if (pending) {
          handlersRef.current.ingestPendingTurn(pending);
          return;
        }
        const text = String(d.transcript || d.text || "");
        if (!text) return;

        const speakerLabel = extractSpeakerLabel(d);
        const mt = String(d.message_type || "").toLowerCase();
        const tt = String(d.type || "").toLowerCase();
        const endOfTurn =
          d.end_of_turn === true ||
          String(d.end_of_turn).toLowerCase() === "true" ||
          tt.includes("final") ||
          mt.includes("final") ||
          mt === "transcript_complete";
        // With format_turns, the unformatted end-of-turn is followed by a formatted one: wait for it.
        const isFinal = endOfTurn && d.turn_is_formatted !== false;

        const relaySegments = isFinal ? parseRelaySegments(d) : null;
        if (relaySegments) {
          handlersRef.current.ingestRelayTurn(typeof d.turn_order === "number" ? d.turn_order : undefined, relaySegments);
        } else if (isFinal) {
          handlersRef.current.ingestFinalTurn({
            text,
            speakerLabel,
            wordLabels: extractWordLabels(d, text),
            turnOrder: typeof d.turn_order === "number" ? d.turn_order : undefined,
          });
        } else {
          handlersRef.current.ingestPartial(text, speakerLabel);
        }
      } catch (err) {
        console.error("[Frontend] WebSocket message error:", err);
      }
    };
  }

  // ---------------------------------------------------------------------------
  // Call controls
  // ---------------------------------------------------------------------------

  /** Start (or resume) the call clock. */
  function startCall() {
    setCallStartedAt((prev) => prev ?? Date.now());
    setCallEndedAt(null);
    callEndedRef.current = false;
  }

  /** Stop the mic without losing the sentence that was still being transcribed. */
  function stopListening() {
    if (wsRef.current) {
      // Turns still waiting for speaker labels keep their provisional label.
      for (const turn of pendingTurnsRef.current) {
        handlersRef.current.ingestFinalTurn({ text: turn.text, speakerLabel: turn.speakerLabel });
      }
      const live = liveRef.current.trim();
      if (live) {
        handlersRef.current.ingestFinalTurn({
          text: live,
          speakerLabel: lastPartialLabelRef.current,
        });
      }
    }
    closeWs();
  }

  /** Put the call on hold: stop listening but keep everything on screen. */
  function pauseCall() {
    if (isListening) stopListening();
  }

  /** Stop listening and freeze the call clock; New call clears the workspace. */
  function endCall() {
    closeWs();
    if (suggestTimerRef.current) clearTimeout(suggestTimerRef.current);
    suggestTimerRef.current = null;
    suggestReqIdRef.current += 1;
    suggestAbortRef.current?.abort();
    setIsFetchingSuggestions(false);
    callEndedRef.current = true;
    setCallEndedAt(Date.now());
  }

  const resetConversation = () => {
    closeWs();
    if (suggestTimerRef.current) clearTimeout(suggestTimerRef.current);
    suggestTimerRef.current = null;
    suggestReqIdRef.current += 1;
    suggestAbortRef.current?.abort();
    extractReqIdRef.current += 1;

    commitTurns([]);
    lastFinalSnapshotRef.current = null;
    setRoleMap({});
    nextVoiceIsStaffRef.current = true;
    setNextVoiceIsStaff(true);

    setSuggestions([]);
    setSuggestionMeta(null);
    setIsFetchingSuggestions(false);

    customerDataRef.current = EMPTY_CUSTOMER;
    fieldSourcesRef.current = {};
    setCustomerData(EMPTY_CUSTOMER);
    setFieldSources({});
    resetHistory();
    lookupKeyRef.current = null;
    lookupPromiseRef.current = null;
    lastCustomerTurnRef.current = null;
    lastSuggestionCasesKeyRef.current = "";
    lastCustomerDataExtractRef.current = "";

    setMoments([]);
    momentKeysRef.current = new Set();
    setCallStartedAt(null);
    setCallEndedAt(null);
    callEndedRef.current = false;
  };

  useEffect(() => {
    return () => {
      if (suggestTimerRef.current) clearTimeout(suggestTimerRef.current);
    };
  }, []);

  const handleCustomerDataChange = (field: CustomerDataField, value: string) => {
    const data = { ...customerDataRef.current, [field]: value };
    const sources = { ...fieldSourcesRef.current, [field]: "manual" as FieldSource };
    customerDataRef.current = data;
    fieldSourcesRef.current = sources;
    setCustomerData(data);
    setFieldSources(sources);
    if (field === "name" || field === "nric_worker_permit_id") {
      // Identity edited by staff: previous results no longer apply.
      resetHistory();
      lookupKeyRef.current = null;
    }
  };

  const obtainCustomerInfo = () => {
    const data = customerDataRef.current;
    const req = nextLookup(null, data);
    if (req) lookupKeyRef.current = req.key;
    void runHistoryLookup({
      name: data.name || undefined,
      nric_worker_permit_id: data.nric_worker_permit_id || undefined,
    });
  };

  const mappedStaffLabel = Object.entries(speakerRoleMap).find(([, r]) => r === "staff")?.[0];
  const mappedCustomerLabel = Object.entries(speakerRoleMap).find(([, r]) => r === "customer")?.[0];
  const hasRoleMapping = Object.keys(speakerRoleMap).length > 0;

  const callActive = callStartedAt !== null && callEndedAt === null;
  const citedIds = useMemo(
    () => new Set(suggestions.flatMap((s) => s.linked_records || [])),
    [suggestions]
  );

  return (
    <div className="shell shell--workspace">
      <SessionHeader
        isLive={isListening}
        isConnecting={isConnecting}
        callerName={customerData.name.trim() || null}
        startedAt={callStartedAt}
        endedAt={callEndedAt}
        micLevel={micLevel}
        canEndCall={callActive && turns.length > 0}
        onEndCall={endCall}
        canPause={callActive && isListening}
        canResume={callActive && !isListening}
        onPause={pauseCall}
        onResume={() => void openWs()}
        canStartNewCall={callEndedAt !== null}
        onNewCall={resetConversation}
        onStart={() => void openWs()}
        onStop={closeWs}
      />

      <main className="workspace-grid">
        <TranscriptPanel
          turns={turns}
          live={live}
          pending={pendingTurns}
          speaking={speaking}
          liveRole={liveRole}
          moments={moments}
          status={isListening ? "Listening" : "Ready"}
          nextVoiceIsStaff={nextVoiceIsStaff}
          hasRoleMapping={hasRoleMapping}
          mappedStaffLabel={mappedStaffLabel}
          mappedCustomerLabel={mappedCustomerLabel}
          onSetNextVoiceRole={setNextVoiceRole}
          onSwapSpeakerRoles={swapSpeakerRoles}
          onFlipTurn={flipTurn}
          transcriptListRef={transcriptListRef}
        />

        <aside className="panel assistance-rail" aria-label="Operator assistance workspace">
          <SuggestionsPanel
            suggestions={suggestions}
            meta={suggestionMeta}
            cases={customerHistoryCases}
            hasTranscript={hasTranscript}
            isLive={isListening}
            isFetchingSuggestions={isFetchingSuggestions}
          />

          <CustomerPanel
            customerData={customerData}
            fieldSources={fieldSources}
            citedIds={citedIds}
            onCustomerDataChange={handleCustomerDataChange}
            onLookup={obtainCustomerInfo}
            customerHistoryStatus={customerHistoryStatus}
            customerHistoryMessage={customerHistory}
            customerHistoryCases={customerHistoryCases}
            historyMeta={historyMeta}
            isLoadingCustomerHistory={customerHistoryStatus === "loading"}
          />
        </aside>
      </main>

    </div>
  );
}
