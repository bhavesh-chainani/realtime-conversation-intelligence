"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CustomerPanel } from "./components/customer-panel";
import { DemoBar } from "./components/demo-bar";
import { SessionHeader } from "./components/session-header";
import { SuggestionsPanel } from "./components/suggestions-panel";
import { TranscriptPanel } from "./components/transcript-panel";
import { createAutopilot, type Autopilot } from "./lib/autopilot.ts";
import { nextLookup } from "./lib/lookup-guard.ts";
import { extractIntroName, extractNric } from "./lib/quick-entities.ts";
import {
  alignTurn,
  deriveLabelMap,
  initialAlignState,
  opposite,
  relabelDiarizedTurns,
  skippedLineIds,
  type AlignSegment,
  type AlignState,
  type ScriptRole,
} from "./lib/script-align.ts";
import { areSimilar } from "./lib/text-normalize.ts";
import {
  isOpenCaseStatus,
  type AutopilotState,
  type CacheStatus,
  type CustomerData,
  type CustomerDataField,
  type CustomerHistoryCase,
  type CustomerHistoryStatus,
  type DemoScenario,
  type DemoScenarioSummary,
  type FieldSource,
  type HistoryMeta,
  type InputMode,
  type Preflight,
  type SpeakerRole,
  type Suggestion,
  type SuggestionMeta,
  type Turn,
} from "./lib/types.ts";

type SpeakerRoleMap = Record<string, ScriptRole>;

type FinalTurnInput = {
  text: string;
  speakerLabel: string | null;
  wordLabels?: Array<string | null>;
  turnOrder?: number;
  source: "mic" | "autopilot";
};

type CachedStep = { suggestions: Suggestion[] };

/** Customer-turn work waits this long so a split second fragment can merge first. */
const SUGGESTION_DEBOUNCE_MS = 250;
/** If the live suggestion hasn't landed this long after the turn ended, show the prepared one. */
const CACHE_RACE_MS = 1300;
/** Max time a suggestion request waits for an in-flight history lookup. */
const LOOKUP_WAIT_MS = 700;
const PREWARM_INTERVAL_MS = 45_000;
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

function normalizeSpeakerLabel(raw: unknown): string | null {
  if (raw == null) return null;
  const label = String(raw).trim().toUpperCase();
  if (!label || label === "UNKNOWN" || label === "NULL" || label === "NONE") {
    return null;
  }
  return label;
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
  const firstRole: ScriptRole = nextVoiceIsStaff ? "staff" : "customer";
  const secondRole: ScriptRole = firstRole === "staff" ? "customer" : "staff";

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
  const cognitoDomain = process.env.NEXT_PUBLIC_COGNITO_DOMAIN || "";
  const cognitoClientId = process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID || "";
  const cognitoRedirectUri =
    process.env.NEXT_PUBLIC_COGNITO_REDIRECT_URI || "http://localhost:3000";
  const cognitoLogoutUri =
    process.env.NEXT_PUBLIC_COGNITO_LOGOUT_URI || "http://localhost:3000";
  const cognitoResponseType = process.env.NEXT_PUBLIC_COGNITO_RESPONSE_TYPE || "token";
  const cognitoScope = process.env.NEXT_PUBLIC_COGNITO_SCOPE || "openid email profile";
  const useAsyncInferenceJobs = process.env.NEXT_PUBLIC_USE_ASYNC_JOBS === "true";

  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [live, setLive] = useState("");
  const [liveRole, setLiveRole] = useState<SpeakerRole>("unknown");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [suggestionMeta, setSuggestionMeta] = useState<SuggestionMeta | null>(null);
  const [isFetchingSuggestions, setIsFetchingSuggestions] = useState(false);
  const [customerHistoryStatus, setCustomerHistoryStatus] =
    useState<CustomerHistoryStatus>("idle");
  const [customerHistory, setCustomerHistory] = useState("");
  const [customerHistoryCases, setCustomerHistoryCases] = useState<CustomerHistoryCase[]>([]);
  const [historyMeta, setHistoryMeta] = useState<HistoryMeta | null>(null);
  const [isLoadingCustomerHistory, setIsLoadingCustomerHistory] = useState(false);
  const [customerData, setCustomerData] = useState<CustomerData>(EMPTY_CUSTOMER);
  const [fieldSources, setFieldSources] = useState<Partial<Record<CustomerDataField, FieldSource>>>({});
  const [isListening, setIsListening] = useState(false);
  const [speakerRoleMap, setSpeakerRoleMap] = useState<SpeakerRoleMap>({});
  const [nextVoiceIsStaff, setNextVoiceIsStaff] = useState(true);
  const [resetNonce, setResetNonce] = useState(0);

  // Demo mode
  const [demoEnabled, setDemoEnabled] = useState(false);
  const [scenarios, setScenarios] = useState<DemoScenarioSummary[] | null>(null);
  const [scenarioId, setScenarioId] = useState<string>("");
  const [scenario, setScenario] = useState<DemoScenario | null>(null);
  const [inputMode, setInputMode] = useState<InputMode>("live");
  const [autopilotState, setAutopilotState] = useState<AutopilotState>("idle");
  const [speed, setSpeed] = useState(1);
  const [stepMode, setStepMode] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [skippedLines, setSkippedLines] = useState<string[]>([]);
  const [cacheStatus, setCacheStatus] = useState<CacheStatus | null>(null);
  const [isBuildingCache, setIsBuildingCache] = useState(false);
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [isCheckingPreflight, setIsCheckingPreflight] = useState(false);

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
  const sessionIdRef = useRef<string | null>(null);
  const debugRef = useRef(false);

  // Conversation state mirrored in refs so async handlers never read stale values.
  const turnsRef = useRef<Turn[]>([]);
  const alignStateRef = useRef<AlignState>(initialAlignState());
  /** State before the latest final, so a re-sent (formatted) version can replace it. */
  const lastFinalSnapshotRef = useRef<{ key: string; turns: Turn[]; align: AlignState } | null>(null);
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

  const scenarioRef = useRef<DemoScenario | null>(null);
  const cacheStepsRef = useRef<Record<string, CachedStep>>({});
  const autopilotRef = useRef<Autopilot | null>(null);
  const speedRef = useRef(1);
  const preflightReqIdRef = useRef(0);
  const stepModeRef = useRef(false);

  useEffect(() => {
    const el = transcriptListRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [turns, live]);

  useEffect(() => {
    speakerRoleMapRef.current = speakerRoleMap;
  }, [speakerRoleMap]);

  useEffect(() => {
    nextVoiceIsStaffRef.current = nextVoiceIsStaff;
  }, [nextVoiceIsStaff]);

  useEffect(() => {
    try {
      localStorage.removeItem("API_AUTH_TOKEN");
    } catch {}
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    debugRef.current = params.get("debug") === "1";
    setDemoEnabled(process.env.NEXT_PUBLIC_DEMO_MODE === "true" || params.get("demo") === "1");
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const hash = window.location.hash.startsWith("#")
        ? window.location.hash.slice(1)
        : "";
      if (hash) {
        const params = new URLSearchParams(hash);
        const accessToken = params.get("access_token") || "";
        if (accessToken) {
          localStorage.setItem("AUTH_ACCESS_TOKEN", accessToken);
          window.history.replaceState({}, document.title, window.location.pathname);
        }
      }
      const storedToken =
        localStorage.getItem("AUTH_ACCESS_TOKEN") ||
        localStorage.getItem("access_token") ||
        localStorage.getItem("id_token") ||
        "";
      setIsAuthenticated(Boolean(storedToken.trim()));
    } catch {
      setIsAuthenticated(false);
    }
  }, []);

  const transcriptText = useMemo(() => formatLabeledTranscript(turns), [turns]);
  const hasTranscript = transcriptText.trim().length >= 10;

  const getAuthHeaders = (): Record<string, string> => {
    const headers: Record<string, string> = {};
    try {
      const storedToken =
        localStorage.getItem("AUTH_ACCESS_TOKEN") ||
        localStorage.getItem("access_token") ||
        localStorage.getItem("id_token") ||
        "";
      if (storedToken.trim()) {
        headers.Authorization = `Bearer ${storedToken.trim()}`;
      }
    } catch {}
    return headers;
  };

  useEffect(() => {
    let cancelled = false;

    async function createSession() {
      sessionIdRef.current = null;
      try {
        const res = await fetch(`${backendUrl}/sessions/`, {
          method: "POST",
          headers: { ...getAuthHeaders() },
        });
        if (!res.ok) return;
        const data = await res.json();
        const sid = typeof data.session_id === "string" ? data.session_id : "";
        if (!cancelled && sid) sessionIdRef.current = sid;
      } catch {
        // optional
      }
    }

    createSession();
    return () => {
      cancelled = true;
    };
  }, [backendUrl, isAuthenticated, resetNonce]);

  async function pollInferenceJob(jobId: string): Promise<Record<string, unknown>> {
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      const res = await fetch(`${backendUrl}/queue/jobs/${encodeURIComponent(jobId)}`, {
        headers: { ...getAuthHeaders() },
      });
      if (!res.ok) throw new Error(`job status ${res.status}`);
      const d = (await res.json()) as Record<string, unknown>;
      if (d.status === "completed" && d.result && typeof d.result === "object") {
        return d.result as Record<string, unknown>;
      }
      if (d.status === "failed") {
        const err = typeof d.error === "string" ? d.error : "Inference job failed";
        throw new Error(err);
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    throw new Error("Inference job timed out");
  }

  const loginWithCognito = () => {
    if (!cognitoDomain || !cognitoClientId) return;
    const base = cognitoDomain.startsWith("http") ? cognitoDomain : `https://${cognitoDomain}`;
    const url = new URL("/login", base);
    url.searchParams.set("client_id", cognitoClientId);
    url.searchParams.set("response_type", cognitoResponseType);
    url.searchParams.set("scope", cognitoScope);
    url.searchParams.set("redirect_uri", cognitoRedirectUri);
    window.location.href = url.toString();
  };

  const logout = () => {
    try {
      localStorage.removeItem("AUTH_ACCESS_TOKEN");
      localStorage.removeItem("access_token");
      localStorage.removeItem("id_token");
    } catch {}
    setIsAuthenticated(false);
    if (!cognitoDomain || !cognitoClientId) return;
    const base = cognitoDomain.startsWith("http") ? cognitoDomain : `https://${cognitoDomain}`;
    const url = new URL("/logout", base);
    url.searchParams.set("client_id", cognitoClientId);
    url.searchParams.set("logout_uri", cognitoLogoutUri);
    window.location.href = url.toString();
  };

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
    setIsLoadingCustomerHistory(false);
  };

  const profilePayload = (): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const [key, value] of Object.entries(customerDataRef.current)) {
      if (value.trim()) out[key] = value.trim();
    }
    if (historyMatchRef.current) out.record_match = historyMatchRef.current;
    return out;
  };

  // ---------------------------------------------------------------------------
  // Suggestions (live call raced against the prepared demo cache)
  // ---------------------------------------------------------------------------

  const cachedStepFor = (turn: Turn): CachedStep | null => {
    if (!scenarioRef.current || turn.roleSource !== "script" || turn.alignConfidence !== "high") return null;
    if (!turn.scriptLineId) return null;
    return cacheStepsRef.current[turn.scriptLineId] ?? null;
  };

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

    const cached = cachedStepFor(turn);
    let shown: "none" | "instant" | "live" = "none";
    const showCached = () => {
      if (!cached || shown !== "none" || reqId !== suggestReqIdRef.current) return;
      shown = "instant";
      setSuggestions(cached.suggestions.slice(0, MAX_SUGGESTIONS));
      setSuggestionMeta({
        origin: "instant",
        latencyMs: performance.now() - turn.committedAt,
        lineId: turn.scriptLineId,
      });
    };
    const raceTimer = cached
      ? setTimeout(showCached, Math.max(0, CACHE_RACE_MS - (performance.now() - turn.committedAt)))
      : null;

    setIsFetchingSuggestions(true);
    try {
      const payload = {
        context,
        max_suggestions: MAX_SUGGESTIONS,
        session_id: sessionIdRef.current || undefined,
        customer_profile: Object.keys(profile).length ? profile : undefined,
        customer_history: cases.length ? cases : undefined,
        scenario_id: scenarioRef.current?.id,
        script_step: turn.scriptLineId,
      };

      let data: Record<string, unknown>;
      if (useAsyncInferenceJobs) {
        const er = await fetch(`${backendUrl}/queue/suggestions`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...getAuthHeaders() },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        const ej = await er.json();
        if (!er.ok || typeof ej.job_id !== "string") {
          throw new Error(`Suggestion enqueue failed: ${er.status}`);
        }
        data = await pollInferenceJob(ej.job_id as string);
      } else {
        const res = await fetch(`${backendUrl}/suggest`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...getAuthHeaders() },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(`Suggestion request failed: ${res.status}`);
        data = await res.json();
      }
      if (reqId !== suggestReqIdRef.current) return;

      const list = Array.isArray(data.suggestions)
        ? (data.suggestions as Suggestion[]).slice(0, MAX_SUGGESTIONS)
        : [];
      const timings = (data.timings || {}) as { llm_ms?: number; model?: string };
      if (data.fallback) {
        // Prefer a prepared, scenario-specific card over generic fallback text.
        if (cached) showCached();
        else if (shown === "none" && list.length) {
          setSuggestions(list);
          setSuggestionMeta({ origin: "fallback", latencyMs: performance.now() - turn.committedAt });
        }
        return;
      }
      if (list.length === 0) {
        // Router chose not to suggest: keep whatever is on screen.
        showCached();
        return;
      }
      shown = "live";
      setSuggestions(list);
      setSuggestionMeta({
        origin: "live",
        latencyMs: performance.now() - turn.committedAt,
        llmMs: timings.llm_ms,
        model: timings.model,
        lineId: turn.scriptLineId,
      });
    } catch (err) {
      if (controller.signal.aborted) return;
      console.error("[Frontend] Failed to fetch suggestions:", err);
      showCached();
    } finally {
      if (raceTimer) clearTimeout(raceTimer);
      if (reqId === suggestReqIdRef.current) setIsFetchingSuggestions(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Customer data: LLM extraction + history lookup
  // ---------------------------------------------------------------------------

  const runHistoryLookup = async (args: { name?: string; nric_worker_permit_id?: string }) => {
    const reqId = ++lookupReqIdRef.current;
    setIsLoadingCustomerHistory(true);
    setCustomerHistoryStatus("loading");

    try {
      const res = await fetch(`${backendUrl}/customer-history`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({
          name: args.name || undefined,
          nric_worker_permit_id: args.nric_worker_permit_id || undefined,
          session_id: sessionIdRef.current || undefined,
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

      historyCasesRef.current = cases;
      historyMatchRef.current = matchedOn;
      setCustomerHistoryStatus(normalizedStatus);
      setCustomerHistory(summary || message || "No customer history found.");
      setCustomerHistoryCases(cases);
      setHistoryMeta(
        normalizedStatus === "ok"
          ? {
              openCount:
                typeof body.open_count === "number"
                  ? body.open_count
                  : cases.filter((c) => isOpenCaseStatus(c.status)).length,
              companies: Array.isArray(body.companies)
                ? body.companies.map(String)
                : Array.from(new Set(cases.map((c) => c.company).filter(Boolean))),
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
    } finally {
      if (reqId === lookupReqIdRef.current) setIsLoadingCustomerHistory(false);
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
      let data: Record<string, unknown>;
      const body = JSON.stringify({
        conversation_transcript: context,
        session_id: sessionIdRef.current || undefined,
      });
      if (useAsyncInferenceJobs) {
        const er = await fetch(`${backendUrl}/queue/extract-customer-data`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...getAuthHeaders() },
          body,
        });
        const ej = await er.json();
        if (!er.ok || typeof ej.job_id !== "string") {
          console.error("[Frontend] extract enqueue failed:", er.status);
          return;
        }
        data = await pollInferenceJob(ej.job_id as string);
      } else {
        const res = await fetch(`${backendUrl}/extract-customer-data`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...getAuthHeaders() },
          body,
        });
        if (!res.ok) return;
        data = await res.json();
      }
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
  // Turn ingestion (shared by microphone and autopilot)
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

  const labelRoleFor = (label: string | null): ScriptRole | null => {
    if (!label) return null;
    const mapped = speakerRoleMapRef.current[label];
    if (mapped) return mapped;
    // With a script, unseen labels are mapped by alignment votes instead of the toggle.
    if (scenarioRef.current) return null;
    const resolved = resolveSpeakerRole(label, speakerRoleMapRef.current, nextVoiceIsStaffRef.current);
    if (resolved.map !== speakerRoleMapRef.current) setRoleMap(resolved.map);
    return resolved.role === "unknown" ? null : resolved.role;
  };

  const ingestPartial = (text: string, speakerLabel: string | null) => {
    const trimmed = text.trim();
    setLive(trimmed);
    liveRef.current = trimmed;
    const expected = scenarioRef.current?.lines[alignStateRef.current.cursor]?.role;
    setLiveRole(expected ?? labelRoleFor(speakerLabel) ?? "unknown");
  };

  const ingestFinalTurn = (input: FinalTurnInput) => {
    const text = input.text.trim();
    setLive("");
    liveRef.current = "";
    setLiveRole("unknown");
    if (!text) return;

    // A re-sent version of the latest turn (formatted text, same turn_order) replaces it.
    let baseTurns = turnsRef.current;
    let baseAlign = alignStateRef.current;
    const last = baseTurns[baseTurns.length - 1];
    const snapshot = lastFinalSnapshotRef.current;
    const isResend =
      !!snapshot &&
      !!last &&
      (input.turnOrder !== undefined
        ? snapshot.key === `order:${input.turnOrder}`
        : input.source === "mic" && last.roleSource !== "manual" && areSimilar(text, last.text));
    if (isResend && snapshot) {
      baseTurns = snapshot.turns;
      baseAlign = snapshot.align;
    } else {
      lastFinalSnapshotRef.current = {
        key: input.turnOrder !== undefined ? `order:${input.turnOrder}` : `turn:${baseTurns.length}`,
        turns: baseTurns,
        align: baseAlign,
      };
    }

    const prevTurn = [...baseTurns].reverse().find((t) => t.role !== "unknown");
    const prevRole = prevTurn ? (prevTurn.role as ScriptRole) : null;
    const script = scenarioRef.current;

    let segments: AlignSegment[];
    let nextAlign = baseAlign;
    if (script) {
      const res = alignTurn(script.lines, baseAlign, {
        text,
        speakerLabel: input.speakerLabel,
        wordLabels: input.wordLabels,
        labelRole: labelRoleFor,
        prevRole,
      });
      segments = res.segments;
      nextAlign = res.state;
    } else {
      segments = [
        {
          text,
          role: labelRoleFor(input.speakerLabel),
          source: "diarization",
          lineId: null,
          score: 0,
          confidence: "none",
          speakerLabel: input.speakerLabel,
        },
      ];
    }

    const committedAt = performance.now();
    const newTurns: Turn[] = segments.map((seg) => ({
      id: newTurnId(),
      text: seg.text,
      speakerLabel: seg.speakerLabel,
      role: seg.role ?? "unknown",
      roleSource: seg.source,
      scriptLineId: seg.lineId ?? undefined,
      alignScore: seg.lineId ? seg.score : undefined,
      alignConfidence: seg.confidence,
      turnOrder: input.turnOrder,
      committedAt,
    }));

    let allTurns = [...baseTurns, ...newTurns];
    if (script) {
      const corrected = deriveLabelMap(nextAlign.labelVotes, speakerRoleMapRef.current);
      if (corrected) {
        setRoleMap(corrected);
        allTurns = relabelDiarizedTurns(allTurns, corrected);
      }
      alignStateRef.current = nextAlign;
      setCursor(nextAlign.cursor);
      setSkippedLines(skippedLineIds(script.lines, nextAlign));
    }
    commitTurns(allTurns);

    if (debugRef.current) {
      console.table(
        segments.map((s) => ({
          text: s.text.slice(0, 60),
          role: s.role,
          source: s.source,
          line: s.lineId,
          score: s.score.toFixed(2),
          confidence: s.confidence,
          label: s.speakerLabel,
          cursor: nextAlign.cursor,
        }))
      );
    }

    // Without a script, unlabelled turns may be the customer too, so they also trigger guidance.
    const lastCustomer = [...newTurns]
      .reverse()
      .find((t) => t.role === "customer" || (!script && t.role === "unknown"));
    if (lastCustomer) handleCustomerTurn(lastCustomer);
  };

  // Long-lived callbacks (WebSocket, autopilot timers) call through this ref to the latest closures.
  const handlersRef = useRef({ ingestFinalTurn, ingestPartial });
  handlersRef.current = { ingestFinalTurn, ingestPartial };

  // ---------------------------------------------------------------------------
  // Speaker controls
  // ---------------------------------------------------------------------------

  const swapSpeakerRoles = useCallback(() => {
    const next: SpeakerRoleMap = {};
    for (const [label, role] of Object.entries(speakerRoleMapRef.current)) {
      next[label] = opposite(role);
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

  /** Staff correction of one turn; also counts as strong evidence for its diarization label. */
  const flipTurn = (turnId: string) => {
    const target = turnsRef.current.find((t) => t.id === turnId);
    if (!target) return;
    const role: ScriptRole = target.role === "staff" ? "customer" : "staff";
    const updated: Turn = { ...target, role, roleSource: "manual" };
    let next = turnsRef.current.map((t) => (t.id === turnId ? updated : t));

    if (target.speakerLabel) {
      const align = alignStateRef.current;
      const votes = { ...align.labelVotes };
      const prev = votes[target.speakerLabel] ?? { staff: 0, customer: 0 };
      votes[target.speakerLabel] = { ...prev, [role]: prev[role] + 2 };
      alignStateRef.current = { ...align, labelVotes: votes };
      const corrected = deriveLabelMap(votes, speakerRoleMapRef.current);
      if (corrected) {
        setRoleMap(corrected);
        next = relabelDiarizedTurns(next, corrected);
      }
    }
    commitTurns(next);
    if (role === "customer" && turnsRef.current[turnsRef.current.length - 1]?.id === turnId) {
      handleCustomerTurn(updated);
    }
  };

  // ---------------------------------------------------------------------------
  // Microphone streaming (AssemblyAI v3)
  // ---------------------------------------------------------------------------

  function closeWs() {
    streamAttemptRef.current += 1;

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
    setLiveRole("unknown");
    setIsListening(false);
  }

  async function openWs() {
    closeWs();
    stopAutopilot();
    const myAttempt = streamAttemptRef.current;

    // A new stream may assign A/B differently: forget the old label map and votes.
    setRoleMap({});
    alignStateRef.current = { ...alignStateRef.current, labelVotes: {} };
    nextVoiceIsStaffRef.current = true;
    setNextVoiceIsStaff(true);
    setLiveRole("unknown");
    lastFinalSnapshotRef.current = null;
    if (demoEnabled) void prewarm();

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

    function pcmEncode(input: Float32Array) {
      const out = new Int16Array(input.length);
      for (let i = 0; i < input.length; i += 1) {
        out[i] = Math.max(-1, Math.min(1, input[i])) * 0x7fff;
      }
      return out.buffer;
    }

    let streamingToken = "";
    let keytermsPrompt: string[] = [];
    let sttPrompt = "";
    let speechModel = "";
    try {
      const scenarioParam = scenarioRef.current ? `?scenario=${encodeURIComponent(scenarioRef.current.id)}` : "";
      const res = await fetch(`${backendUrl}/assemblyai-token${scenarioParam}`, {
        headers: getAuthHeaders(),
      });
      if (!res.ok) {
        alert("Failed to obtain streaming token from backend.");
        closeWs();
        return;
      }
      const payload = await res.json();
      streamingToken = String(payload.token || "").trim();
      keytermsPrompt = Array.isArray(payload.keyterms_prompt)
        ? payload.keyterms_prompt.map((t: unknown) => String(t))
        : [];
      sttPrompt = typeof payload.prompt === "string" ? payload.prompt : "";
      speechModel = typeof payload.speech_model === "string" ? payload.speech_model : "";
      if (!streamingToken) {
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

    const ws = new WebSocket(`wss://streaming.assemblyai.com/v3/ws?${params}`);
    if (myAttempt !== streamAttemptRef.current) {
      try {
        ws.close();
      } catch {}
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => setIsListening(true);
    ws.onclose = (evt) => {
      if (evt.code !== 1000 && evt.code !== 1005) console.warn("[STT] Closed", evt.code, evt.reason);
      setIsListening(false);
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
        if (d.type === "Begin") {
          console.info("[STT] Session started", d);
          return;
        }
        if (d.type === "Error" || d.error) {
          // Fail loudly: a silent STT failure looks like a frozen demo.
          console.error("[STT] Error", d);
          alert(`Transcription error: ${String(d.error || "unknown")}\nSwitch to Autopilot to continue.`);
          closeWs();
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

        if (isFinal) {
          handlersRef.current.ingestFinalTurn({
            text,
            speakerLabel,
            wordLabels: extractWordLabels(d, text),
            turnOrder: typeof d.turn_order === "number" ? d.turn_order : undefined,
            source: "mic",
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
  // Demo controls
  // ---------------------------------------------------------------------------

  async function prewarm() {
    try {
      await fetch(`${backendUrl}/demo/prewarm`, { method: "POST", headers: getAuthHeaders() });
    } catch {}
  }

  function stopAutopilot() {
    autopilotRef.current?.stop();
    autopilotRef.current = null;
    setAutopilotState("idle");
  }

  function startAutopilot() {
    const script = scenarioRef.current;
    if (!script) return;
    closeWs();
    autopilotRef.current?.stop();
    lastFinalSnapshotRef.current = null;
    const ap = createAutopilot(
      script.lines,
      alignStateRef.current.cursor,
      {
        onPartial: (_i, text) => handlersRef.current.ingestPartial(text, null),
        onFinal: (i) =>
          handlersRef.current.ingestFinalTurn({ text: script.lines[i].text, speakerLabel: null, source: "autopilot" }),
        onWaiting: () => setAutopilotState("waiting"),
        onDone: () => setAutopilotState("done"),
      },
      {
        wpm: script.autopilot?.wpm ?? 185,
        gapMs: script.autopilot?.gap_ms ?? [450, 850],
        speed: speedRef.current,
        stepMode: stepModeRef.current,
      }
    );
    autopilotRef.current = ap;
    ap.start();
    setAutopilotState("running");
    void prewarm();
  }

  const resetConversation = () => {
    stopAutopilot();
    closeWs();
    if (suggestTimerRef.current) clearTimeout(suggestTimerRef.current);
    suggestTimerRef.current = null;
    suggestReqIdRef.current += 1;
    suggestAbortRef.current?.abort();
    extractReqIdRef.current += 1;

    commitTurns([]);
    alignStateRef.current = initialAlignState();
    lastFinalSnapshotRef.current = null;
    setCursor(0);
    setSkippedLines([]);
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

    setResetNonce((n) => n + 1);
  };

  const loadCache = useCallback(
    async (id: string) => {
      try {
        const res = await fetch(`${backendUrl}/demo/cache/${encodeURIComponent(id)}`, {
          headers: getAuthHeaders(),
        });
        if (!res.ok) return;
        const body = await res.json();
        cacheStepsRef.current = body.steps && typeof body.steps === "object" ? body.steps : {};
        setCacheStatus({
          built: Boolean(body.built),
          fresh: Boolean(body.fresh),
          steps: Number(body.steps ? Object.keys(body.steps).length : 0),
          total: Number(body.total || 0),
          built_at: body.built_at,
          model: body.model,
        });
      } catch {}
    },
    [backendUrl]
  );

  const runPreflight = useCallback(
    async (id: string) => {
      // A slow earlier check (e.g. a cold LLM) must not overwrite a newer result.
      const reqId = ++preflightReqIdRef.current;
      setIsCheckingPreflight(true);
      try {
        const q = id ? `?scenario_id=${encodeURIComponent(id)}` : "";
        const res = await fetch(`${backendUrl}/demo/preflight${q}`, { headers: getAuthHeaders() });
        const body = res.ok ? await res.json() : null;
        if (reqId === preflightReqIdRef.current) setPreflight(body);
      } catch {
        if (reqId === preflightReqIdRef.current) setPreflight(null);
      } finally {
        if (reqId === preflightReqIdRef.current) setIsCheckingPreflight(false);
      }
    },
    [backendUrl]
  );

  const buildCache = async () => {
    if (!scenarioId) return;
    setIsBuildingCache(true);
    try {
      await fetch(`${backendUrl}/demo/cache/${encodeURIComponent(scenarioId)}/build`, {
        method: "POST",
        headers: getAuthHeaders(),
      });
      await loadCache(scenarioId);
      await runPreflight(scenarioId);
    } finally {
      setIsBuildingCache(false);
    }
  };

  // Load the scenario list once demo mode is on.
  useEffect(() => {
    if (!demoEnabled) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${backendUrl}/demo/scenarios`, { headers: getAuthHeaders() });
        if (!res.ok) return;
        const list = (await res.json()) as DemoScenarioSummary[];
        if (cancelled) return;
        let remembered = "";
        try {
          remembered = localStorage.getItem("DEMO_SCENARIO") || "";
        } catch {}
        const initial = list.find((s) => s.id === remembered)?.id ?? list[0]?.id ?? "";
        setScenarioId(initial);
        setScenarios(list);
      } catch {
        console.warn("[Demo] Backend demo endpoints unavailable (is DEMO_MODE=true?)");
        if (!cancelled) setScenarios([]);
      }
    })();
    void prewarm();
    return () => {
      cancelled = true;
    };
  }, [demoEnabled, backendUrl]);

  // Selecting a scenario loads its script + cache and starts a clean conversation.
  useEffect(() => {
    if (!demoEnabled || scenarios === null) return;
    let cancelled = false;
    try {
      localStorage.setItem("DEMO_SCENARIO", scenarioId);
    } catch {}
    resetConversation();
    if (!scenarioId) {
      scenarioRef.current = null;
      setScenario(null);
      cacheStepsRef.current = {};
      setCacheStatus(null);
      setInputMode("live");
      void runPreflight("");
      return;
    }
    (async () => {
      try {
        const res = await fetch(`${backendUrl}/demo/scenarios/${encodeURIComponent(scenarioId)}`, {
          headers: getAuthHeaders(),
        });
        if (!res.ok || cancelled) return;
        const data = (await res.json()) as DemoScenario;
        scenarioRef.current = data;
        setScenario(data);
      } catch {}
      if (cancelled) return;
      await loadCache(scenarioId);
      await runPreflight(scenarioId);
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demoEnabled, scenarios, scenarioId, backendUrl, loadCache, runPreflight]);

  // Keep LLM connections warm while a demo conversation is running.
  const demoActive = isListening || autopilotState === "running" || autopilotState === "waiting";
  useEffect(() => {
    if (!demoEnabled || !demoActive) return;
    const timer = setInterval(() => void prewarm(), PREWARM_INTERVAL_MS);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demoEnabled, demoActive]);

  // Presenter shortcut: → plays the next autopilot line.
  useEffect(() => {
    if (!demoEnabled) return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "ArrowRight" && autopilotRef.current) {
        e.preventDefault();
        autopilotRef.current.next();
        setAutopilotState("running");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [demoEnabled]);

  useEffect(() => {
    return () => {
      autopilotRef.current?.stop();
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

  return (
    <div className="shell shell--workspace">
      <SessionHeader
        isLive={isListening || autopilotState === "running" || autopilotState === "waiting"}
        isAuthenticated={isAuthenticated}
        showAuthButton={Boolean(cognitoDomain && cognitoClientId)}
        showSessionControls={!demoEnabled}
        onLogin={loginWithCognito}
        onLogout={logout}
        onStart={() => {
          void openWs();
        }}
        onStop={closeWs}
      />

      {demoEnabled ? (
        <DemoBar
          scenarios={scenarios ?? []}
          scenarioId={scenarioId}
          onScenarioChange={setScenarioId}
          scenario={scenario}
          inputMode={inputMode}
          onInputModeChange={(mode) => {
            if (mode === "live") stopAutopilot();
            else closeWs();
            setInputMode(mode);
          }}
          isListening={isListening}
          autopilotState={autopilotState}
          onStart={() => {
            if (inputMode === "autopilot") startAutopilot();
            else void openWs();
          }}
          onStop={() => {
            if (inputMode === "autopilot") {
              autopilotRef.current?.pause();
              setAutopilotState("paused");
            } else closeWs();
          }}
          onNextLine={() => {
            if (!autopilotRef.current) startAutopilot();
            else {
              autopilotRef.current.next();
              setAutopilotState("running");
            }
          }}
          onReset={resetConversation}
          speed={speed}
          onSpeedChange={(s) => {
            speedRef.current = s;
            setSpeed(s);
            autopilotRef.current?.setSpeed(s);
          }}
          stepMode={stepMode}
          onStepModeChange={(v) => {
            stepModeRef.current = v;
            setStepMode(v);
            autopilotRef.current?.setStepMode(v);
          }}
          cursor={cursor}
          skippedLines={skippedLines}
          cacheStatus={cacheStatus}
          isBuildingCache={isBuildingCache}
          onBuildCache={() => void buildCache()}
          preflight={preflight}
          isCheckingPreflight={isCheckingPreflight}
          onRunPreflight={() => void runPreflight(scenarioId)}
        />
      ) : null}

      <main className="workspace-grid">
        <TranscriptPanel
          turns={turns}
          live={live}
          liveRole={liveRole}
          status={
            isListening
              ? "Listening"
              : autopilotState === "running" || autopilotState === "waiting"
                ? "Playing script"
                : autopilotState === "paused"
                  ? "Paused"
                  : "Ready"
          }
          scriptGuided={Boolean(scenario)}
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
            isListening={isListening || autopilotState === "running"}
            isFetchingSuggestions={isFetchingSuggestions}
          />

          <CustomerPanel
            customerData={customerData}
            fieldSources={fieldSources}
            onCustomerDataChange={handleCustomerDataChange}
            onLookup={obtainCustomerInfo}
            customerHistoryStatus={customerHistoryStatus}
            customerHistoryMessage={customerHistory}
            customerHistoryCases={customerHistoryCases}
            historyMeta={historyMeta}
            isLoadingCustomerHistory={isLoadingCustomerHistory}
          />
        </aside>
      </main>
    </div>
  );
}
