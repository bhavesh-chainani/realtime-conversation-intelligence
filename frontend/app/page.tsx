"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CustomerPanel } from "./components/customer-panel";
import { SessionHeader } from "./components/session-header";
import { SuggestionsPanel } from "./components/suggestions-panel";
import { TranscriptPanel } from "./components/transcript-panel";

type Suggestion = {
  type?: string;
  text?: string;
  topic?: string;
  confidence?: number;
  details?: {
    possibleConversation?: string;
    operatorResponse?: string;
    suggestedConversation?: string;
    priority?: string;
    [key: string]: any;
  };
};

type CustomerData = {
  name: string;
  nric_worker_permit_id: string;
  address: string;
  purpose_of_call: string;
};

type CustomerHistoryCase = {
  case_id: string;
  company: string;
  type: string;
  status: string;
  summary: string;
};

type CustomerHistoryStatus =
  | "idle"
  | "loading"
  | "invalid_input"
  | "not_configured"
  | "not_found"
  | "ok"
  | "error";

type CustomerDataFields = keyof CustomerData;

type SpeakerRole = "staff" | "customer" | "unknown";

type Turn = {
  id: string;
  text: string;
  speakerLabel: string | null;
  role: SpeakerRole;
};

type SpeakerRoleMap = Record<string, "staff" | "customer">;

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

function resolveSpeakerRole(
  label: string | null,
  map: SpeakerRoleMap,
  nextVoiceIsStaff: boolean
): { role: SpeakerRole; map: SpeakerRoleMap } {
  if (!label) return { role: "unknown", map };

  const existing = map[label];
  if (existing) return { role: existing, map };

  const assigned = new Set(Object.values(map));
  const firstRole: "staff" | "customer" = nextVoiceIsStaff ? "staff" : "customer";
  const secondRole: "staff" | "customer" = firstRole === "staff" ? "customer" : "staff";

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
  const [isFetchingSuggestions, setIsFetchingSuggestions] = useState(false);
  const [customerHistoryStatus, setCustomerHistoryStatus] =
    useState<CustomerHistoryStatus>("idle");
  const [customerHistory, setCustomerHistory] = useState("");
  const [customerHistoryCases, setCustomerHistoryCases] = useState<CustomerHistoryCase[]>([]);
  const [isLoadingCustomerHistory, setIsLoadingCustomerHistory] = useState(false);
  const [customerData, setCustomerData] = useState<CustomerData>({
    name: "",
    nric_worker_permit_id: "",
    address: "",
    purpose_of_call: "",
  });
  const [manuallyEditedFields, setManuallyEditedFields] = useState<Set<CustomerDataFields>>(
    new Set()
  );
  const [isListening, setIsListening] = useState(false);
  const [speakerRoleMap, setSpeakerRoleMap] = useState<SpeakerRoleMap>({});
  const [nextVoiceIsStaff, setNextVoiceIsStaff] = useState(true);

  const manuallyEditedFieldsRef = useRef<Set<CustomerDataFields>>(new Set());
  const wsRef = useRef<WebSocket | null>(null);
  const mediaRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const mediaSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const streamAttemptRef = useRef(0);
  const liveRef = useRef("");
  const lastCustomerDataExtractRef = useRef("");
  const speakerRoleMapRef = useRef<SpeakerRoleMap>({});
  const nextVoiceIsStaffRef = useRef(true);
  const transcriptListRef = useRef<HTMLDivElement>(null);
  const sessionIdRef = useRef<string | null>(null);
  const lastTranscriptRef = useRef("");
  const debounceTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    manuallyEditedFieldsRef.current = manuallyEditedFields;
  }, [manuallyEditedFields]);

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

  useEffect(() => {
    liveRef.current = live;
  }, [live]);

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
  }, [backendUrl, isAuthenticated]);

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

  const swapSpeakerRoles = useCallback(() => {
    setSpeakerRoleMap((prev) => {
      const next: SpeakerRoleMap = {};
      for (const [label, role] of Object.entries(prev)) {
        next[label] = role === "staff" ? "customer" : "staff";
      }
      speakerRoleMapRef.current = next;
      return next;
    });
    setTurns((prev) =>
      prev.map((t) => ({
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
    const myAttempt = streamAttemptRef.current;

    speakerRoleMapRef.current = {};
    nextVoiceIsStaffRef.current = true;
    setSpeakerRoleMap({});
    setNextVoiceIsStaff(true);
    setLiveRole("unknown");

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
    try {
      const res = await fetch(`${backendUrl}/assemblyai-token`, {
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

    const ws = new WebSocket(`wss://streaming.assemblyai.com/v3/ws?${params}`);
    if (myAttempt !== streamAttemptRef.current) {
      try {
        ws.close();
      } catch {}
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => setIsListening(true);
    ws.onclose = () => setIsListening(false);
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
        const text = String(d.transcript || d.text || "");
        if (!text) return;

        const speakerLabel = extractSpeakerLabel(d);
        const mt = String(d.message_type || "").toLowerCase();
        const tt = String(d.type || "").toLowerCase();
        const isFinal =
          d.end_of_turn === true ||
          String(d.end_of_turn).toLowerCase() === "true" ||
          tt.includes("final") ||
          mt.includes("final") ||
          mt === "transcript_complete";

        const normalizeForCompare = (s: string): string =>
          s.toLowerCase().replace(/[^\w\s]/g, "").replace(/\s+/g, " ").trim();

        const normalizeWord = (word: string): string => {
          const numberWords: { [key: string]: string } = {
            zero: "0",
            one: "1",
            two: "2",
            three: "3",
            four: "4",
            five: "5",
            six: "6",
            seven: "7",
            eight: "8",
            nine: "9",
            ten: "10",
            eleven: "11",
            twelve: "12",
            thirteen: "13",
            fourteen: "14",
            fifteen: "15",
            sixteen: "16",
            seventeen: "17",
            eighteen: "18",
            nineteen: "19",
            twenty: "20",
          };
          const lower = word.toLowerCase();
          if (numberWords[lower]) return numberWords[lower];
          if (/^\d+$/.test(word)) return word;
          return lower;
        };

        const areSimilar = (text1: string, text2: string): boolean => {
          if (!text1 || !text2) return false;
          const norm1 = normalizeForCompare(text1);
          const norm2 = normalizeForCompare(text2);
          if (norm1 === norm2) return true;

          const words1 = norm1
            .split(/\s+/)
            .filter((w) => w.length > 0)
            .map(normalizeWord);
          const words2 = norm2
            .split(/\s+/)
            .filter((w) => w.length > 0)
            .map(normalizeWord);

          if (Math.abs(words1.length - words2.length) > 2) return false;

          const set1 = new Set(words1);
          const set2 = new Set(words2);
          let matches = 0;
          for (const word of set1) {
            if (set2.has(word)) matches += 1;
          }
          const minWords = Math.min(set1.size, set2.size);
          if (minWords === 0) return false;
          return matches / minWords >= 0.8;
        };

        const assignRole = (label: string | null): SpeakerRole => {
          const resolved = resolveSpeakerRole(
            label,
            speakerRoleMapRef.current,
            nextVoiceIsStaffRef.current
          );
          if (resolved.map !== speakerRoleMapRef.current) {
            speakerRoleMapRef.current = resolved.map;
            setSpeakerRoleMap(resolved.map);
          }
          return resolved.role;
        };

        if (isFinal) {
          const trimmed = text.trim();
          if (!trimmed) return;
          const normalizedNew = normalizeForCompare(trimmed);
          const normalizedLive = normalizeForCompare(liveRef.current || "");
          const role = assignRole(speakerLabel);

          setTurns((prev) => {
            if (prev.length === 0) {
              return [{ id: newTurnId(), text: trimmed, speakerLabel, role }];
            }
            const last = prev[prev.length - 1];
            const normalizedLast = normalizeForCompare(last.text);

            if (
              normalizedNew === normalizedLive ||
              normalizedNew === normalizedLast ||
              areSimilar(trimmed, liveRef.current || "") ||
              areSimilar(trimmed, last.text)
            ) {
              const mergedLabel = speakerLabel || last.speakerLabel;
              let mergedRole = role;
              if (mergedRole === "unknown" && mergedLabel) {
                mergedRole = speakerRoleMapRef.current[mergedLabel] || last.role || "unknown";
              } else if (mergedRole === "unknown") {
                mergedRole = last.role;
              }
              return [
                ...prev.slice(0, -1),
                {
                  ...last,
                  text: trimmed,
                  speakerLabel: mergedLabel,
                  role: mergedRole,
                },
              ];
            }

            return [...prev, { id: newTurnId(), text: trimmed, speakerLabel, role }];
          });

          setLive("");
          liveRef.current = "";
          setLiveRole("unknown");
        } else {
          const trimmed = text.trim();
          setLive(trimmed);
          liveRef.current = trimmed;
          setLiveRole(assignRole(speakerLabel));
        }
      } catch (err) {
        console.error("[Frontend] WebSocket message error:", err);
      }
    };
  }

  const handleCustomerDataChange = (field: CustomerDataFields, value: string) => {
    setCustomerData((prev) => ({ ...prev, [field]: value }));
    setManuallyEditedFields((prev) => new Set(prev).add(field));
  };

  const extractCustomerData = useCallback(
    async (context: string) => {
      if (!context || context.trim().length < 10) return;

      const normalizedCurrent = context.trim().toLowerCase();
      const normalizedLast = lastCustomerDataExtractRef.current.trim().toLowerCase();
      if (normalizedCurrent === normalizedLast) return;

      try {
        lastCustomerDataExtractRef.current = context;

        let data: Record<string, unknown>;
        if (useAsyncInferenceJobs) {
          const er = await fetch(`${backendUrl}/queue/extract-customer-data`, {
            method: "POST",
            headers: { "Content-Type": "application/json", ...getAuthHeaders() },
            body: JSON.stringify({
              conversation_transcript: context,
              session_id: sessionIdRef.current || undefined,
            }),
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
            body: JSON.stringify({
              conversation_transcript: context,
              session_id: sessionIdRef.current || undefined,
            }),
          });
          if (!res.ok) return;
          data = await res.json();
        }

        const successVal = Boolean(data.success);
        const rawData =
          data.data && typeof data.data === "object" ? (data.data as Record<string, unknown>) : null;
        if (successVal && rawData) {
          setCustomerData((prev) => {
            const updated = { ...prev };
            const currentEditedFields = manuallyEditedFieldsRef.current;
            Object.keys(rawData).forEach((key) => {
              const field = key as CustomerDataFields;
              const extractedValue = rawData[field];
              if (extractedValue == null || extractedValue === "") return;
              if (!currentEditedFields.has(field)) {
                updated[field] = String(extractedValue as string);
              }
            });
            return updated;
          });
        }
      } catch (err) {
        console.error("[Frontend] Failed to extract customer data:", err);
      }
    },
    [backendUrl, useAsyncInferenceJobs]
  );

  const fetchSuggestions = useCallback(
    async (context: string) => {
      if (!context || context.trim().length < 10) {
        setSuggestions([]);
        return;
      }

      const normalizedCurrent = context.trim().toLowerCase();
      const normalizedLast = lastTranscriptRef.current.trim().toLowerCase();
      if (normalizedCurrent === normalizedLast) {
        console.log("[Frontend] Transcript unchanged, skipping suggestion fetch");
        return;
      }

      setIsFetchingSuggestions(true);
      try {
        console.log(
          `[Frontend] Fetching suggestions for transcript (${context.length} chars): "${context.substring(0, 100)}..."`
        );
        lastTranscriptRef.current = context;

        if (useAsyncInferenceJobs) {
          const er = await fetch(`${backendUrl}/queue/suggestions`, {
            method: "POST",
            headers: { "Content-Type": "application/json", ...getAuthHeaders() },
            body: JSON.stringify({
              context,
              max_suggestions: 2,
              session_id: sessionIdRef.current || undefined,
            }),
          });
          const ej = await er.json();
          if (!er.ok || typeof ej.job_id !== "string") {
            console.error("[Frontend] Suggestion enqueue failed:", er.status);
            return;
          }
          const result = await pollInferenceJob(ej.job_id as string);
          const sug = Array.isArray((result as Record<string, unknown>).suggestions)
            ? (result as Record<string, unknown>).suggestions
            : [];
          setSuggestions(sug as Suggestion[]);
        } else {
          const res = await fetch(`${backendUrl}/suggest`, {
            method: "POST",
            headers: { "Content-Type": "application/json", ...getAuthHeaders() },
            body: JSON.stringify({
              context,
              max_suggestions: 2,
              session_id: sessionIdRef.current || undefined,
            }),
          });
          if (res.ok) {
            const data = await res.json();
            setSuggestions(data.suggestions || []);
          } else {
            console.error(`[Frontend] Suggestion request failed: ${res.status}`);
          }
        }
      } catch (err) {
        console.error("[Frontend] Failed to fetch suggestions:", err);
      } finally {
        setIsFetchingSuggestions(false);
      }
    },
    [backendUrl, useAsyncInferenceJobs]
  );

  const obtainCustomerInfo = useCallback(async () => {
    if (isLoadingCustomerHistory) return;

    setIsLoadingCustomerHistory(true);
    setCustomerHistoryStatus("loading");
    setCustomerHistory("");
    setCustomerHistoryCases([]);

    try {
      const res = await fetch(`${backendUrl}/customer-history`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({
          name: customerData.name || undefined,
          nric_worker_permit_id: customerData.nric_worker_permit_id || undefined,
          session_id: sessionIdRef.current || undefined,
        }),
      });
      const body = await res.json();
      if (!res.ok || !body || typeof body !== "object") {
        setCustomerHistoryStatus("error");
        setCustomerHistory("Unable to obtain customer history at the moment.");
        return;
      }

      const status = typeof body.status === "string" ? body.status : "error";
      const summary = typeof body.history_summary === "string" ? body.history_summary : "";
      const message = typeof body.message === "string" ? body.message : "";
      const cases = Array.isArray(body.cases) ? body.cases : [];
      const normalizedStatus: CustomerHistoryStatus =
        status === "invalid_input" ||
        status === "not_configured" ||
        status === "not_found" ||
        status === "ok" ||
        status === "error"
          ? status
          : "error";

      setCustomerHistoryStatus(normalizedStatus);
      setCustomerHistory(summary || message || "No customer history found.");
      setCustomerHistoryCases(
        normalizedStatus === "ok"
          ? cases.map((row: any) => ({
              case_id: String(row.case_id || ""),
              company: String(row.company || ""),
              type: String(row.type || ""),
              status: String(row.status || ""),
              summary: String(row.summary || ""),
            }))
          : []
      );
    } catch (err) {
      console.error("[Frontend] Failed to obtain customer history:", err);
      setCustomerHistoryStatus("error");
      setCustomerHistory("Unable to obtain customer history at the moment.");
      setCustomerHistoryCases([]);
    } finally {
      setIsLoadingCustomerHistory(false);
    }
  }, [backendUrl, customerData.name, customerData.nric_worker_permit_id, isLoadingCustomerHistory]);

  useEffect(() => {
    setCustomerHistoryStatus("idle");
    setCustomerHistory("");
    setCustomerHistoryCases([]);
  }, [customerData.name, customerData.nric_worker_permit_id]);

  useEffect(() => {
    if (!transcriptText || transcriptText.trim().length < 10) {
      setSuggestions([]);
      setIsFetchingSuggestions(false);
      lastTranscriptRef.current = "";
      lastCustomerDataExtractRef.current = "";
      if (debounceTimeoutRef.current) {
        clearTimeout(debounceTimeoutRef.current);
        debounceTimeoutRef.current = null;
      }
      return;
    }

    if (debounceTimeoutRef.current) {
      clearTimeout(debounceTimeoutRef.current);
    }

    debounceTimeoutRef.current = setTimeout(() => {
      const normalizedCurrent = transcriptText.trim().toLowerCase();
      const normalizedLast = lastTranscriptRef.current.trim().toLowerCase();
      if (normalizedCurrent !== normalizedLast) {
        fetchSuggestions(transcriptText);
        extractCustomerData(transcriptText);
      }
      debounceTimeoutRef.current = null;
    }, 1500);

    return () => {
      if (debounceTimeoutRef.current) {
        clearTimeout(debounceTimeoutRef.current);
        debounceTimeoutRef.current = null;
      }
    };
  }, [transcriptText, fetchSuggestions, extractCustomerData]);

  const mappedStaffLabel = Object.entries(speakerRoleMap).find(([, r]) => r === "staff")?.[0];
  const mappedCustomerLabel = Object.entries(speakerRoleMap).find(([, r]) => r === "customer")?.[0];
  const hasRoleMapping = Object.keys(speakerRoleMap).length > 0;

  return (
    <div className="shell shell--workspace">
      <SessionHeader
        isListening={isListening}
        isAuthenticated={isAuthenticated}
        showAuthButton={Boolean(cognitoDomain && cognitoClientId)}
        onLogin={loginWithCognito}
        onLogout={logout}
        onStart={() => {
          void openWs();
        }}
        onStop={closeWs}
      />

      <main className="workspace-grid">
        <TranscriptPanel
          turns={turns}
          live={live}
          liveRole={liveRole}
          isListening={isListening}
          nextVoiceIsStaff={nextVoiceIsStaff}
          hasRoleMapping={hasRoleMapping}
          mappedStaffLabel={mappedStaffLabel}
          mappedCustomerLabel={mappedCustomerLabel}
          onSetNextVoiceRole={setNextVoiceRole}
          onSwapSpeakerRoles={swapSpeakerRoles}
          transcriptListRef={transcriptListRef}
        />

        <aside className="panel assistance-rail" aria-label="Operator assistance workspace">
          <SuggestionsPanel
            suggestions={suggestions}
            hasTranscript={hasTranscript}
            isListening={isListening}
            isFetchingSuggestions={isFetchingSuggestions}
          />

          <CustomerPanel
            customerData={customerData}
            onCustomerDataChange={handleCustomerDataChange}
            onLookup={obtainCustomerInfo}
            customerHistoryStatus={customerHistoryStatus}
            customerHistoryMessage={customerHistory}
            customerHistoryCases={customerHistoryCases}
            isLoadingCustomerHistory={isLoadingCustomerHistory}
          />
        </aside>
      </main>
    </div>
  );
}
