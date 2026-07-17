"use client";
import { useEffect, useMemo, useRef, useState, useCallback } from "react";

function useLocalStorage(key: string, initial: string) {
  const [value, setValue] = useState<string>(() => {
    if (typeof window === 'undefined') return initial;
    return localStorage.getItem(key) ?? initial;
  });
  useEffect(() => { try { localStorage.setItem(key, value); } catch {} }, [key, value]);
  return [value, setValue] as const;
}

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
    (typeof window !== 'undefined' ? (localStorage.getItem('BACKEND_URL') || 'http://localhost:8000') : 'http://localhost:8000');
  const cognitoDomain = process.env.NEXT_PUBLIC_COGNITO_DOMAIN || '';
  const cognitoClientId = process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID || '';
  const cognitoRedirectUri = process.env.NEXT_PUBLIC_COGNITO_REDIRECT_URI || 'http://localhost:3000';
  const cognitoLogoutUri = process.env.NEXT_PUBLIC_COGNITO_LOGOUT_URI || 'http://localhost:3000';
  const cognitoResponseType = process.env.NEXT_PUBLIC_COGNITO_RESPONSE_TYPE || 'token';
  const cognitoScope = process.env.NEXT_PUBLIC_COGNITO_SCOPE || 'openid email profile';
  const useAsyncInferenceJobs =
    process.env.NEXT_PUBLIC_USE_ASYNC_JOBS === 'true';

  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [live, setLive] = useState<string>('');
  const [liveRole, setLiveRole] = useState<SpeakerRole>('unknown');
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [customerHistory, setCustomerHistory] = useState<string>("");
  const [isLoadingCustomerHistory, setIsLoadingCustomerHistory] = useState(false);
  const [customerData, setCustomerData] = useState<CustomerData>({
    name: '',
    nric_worker_permit_id: '',
    address: '',
    purpose_of_call: ''
  });
  const [manuallyEditedFields, setManuallyEditedFields] = useState<Set<CustomerDataFields>>(new Set());
  const [isListening, setIsListening] = useState(false);
  const [speakerRoleMap, setSpeakerRoleMap] = useState<SpeakerRoleMap>({});
  /** When armed, the next unseen speaker label locks to Staff (default) or Customer. */
  const [nextVoiceIsStaff, setNextVoiceIsStaff] = useState(true);
  const manuallyEditedFieldsRef = useRef<Set<CustomerDataFields>>(new Set());
  const wsRef = useRef<WebSocket | null>(null);
  const mediaRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const mediaSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  /** Bumps on each new `openWs` so stale async continuations do not attach after Stop or a second Start. */
  const streamAttemptRef = useRef(0);
  const liveRef = useRef<string>(''); // Track live text for deduplication checks
  const lastCustomerDataExtractRef = useRef<string>(''); // Track last transcript we extracted from
  const speakerRoleMapRef = useRef<SpeakerRoleMap>({});
  const nextVoiceIsStaffRef = useRef(true);
  const transcriptListRef = useRef<HTMLDivElement | null>(null);

  // Keep ref in sync with state
  useEffect(() => {
    manuallyEditedFieldsRef.current = manuallyEditedFields;
  }, [manuallyEditedFields]);

  // Auto-scroll transcript to the latest turn / partial as the conversation grows.
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

  // Cleanup from earlier auth-token based UI versions.
  useEffect(() => {
    try {
      localStorage.removeItem('API_AUTH_TOKEN');
    } catch {}
  }, []);

  // Cognito Hosted UI tokens (hash redirect) + initial auth flag from storage.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      const hash = window.location.hash.startsWith('#')
        ? window.location.hash.slice(1)
        : '';
      if (hash) {
        const params = new URLSearchParams(hash);
        const accessToken = params.get('access_token') || '';
        if (accessToken) {
          localStorage.setItem('AUTH_ACCESS_TOKEN', accessToken);
          window.history.replaceState({}, document.title, window.location.pathname);
        }
      }
      const storedToken =
        localStorage.getItem('AUTH_ACCESS_TOKEN') ||
        localStorage.getItem('access_token') ||
        localStorage.getItem('id_token') ||
        '';
      setIsAuthenticated(Boolean(storedToken.trim()));
    } catch {
      setIsAuthenticated(false);
    }
  }, []);

  const transcriptText = useMemo(() => formatLabeledTranscript(turns), [turns]);

  // Keep liveRef in sync with live state
  useEffect(() => {
    liveRef.current = live;
  }, [live]);

  const getAuthHeaders = (): Record<string, string> => {
    const headers: Record<string, string> = {};
    try {
      const storedToken =
        localStorage.getItem('AUTH_ACCESS_TOKEN') ||
        localStorage.getItem('access_token') ||
        localStorage.getItem('id_token') ||
        '';
      if (storedToken.trim()) {
        headers.Authorization = `Bearer ${storedToken.trim()}`;
      }
    } catch {}
    return headers;
  };

  const sessionIdRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function createSession() {
      sessionIdRef.current = null;
      try {
        const res = await fetch(`${backendUrl}/sessions/`, {
          method: 'POST',
          headers: { ...getAuthHeaders() },
        });
        if (!res.ok) return;
        const data = await res.json();
        const sid = typeof data.session_id === 'string' ? data.session_id : '';
        if (!cancelled && sid) sessionIdRef.current = sid;
      } catch {
        /* optional */
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
      const res = await fetch(
        `${backendUrl}/queue/jobs/${encodeURIComponent(jobId)}`,
        { headers: { ...getAuthHeaders() } }
      );
      if (!res.ok) throw new Error(`job status ${res.status}`);
      const d = (await res.json()) as Record<string, unknown>;
      if (d.status === 'completed' && d.result && typeof d.result === 'object') {
        return d.result as Record<string, unknown>;
      }
      if (d.status === 'failed') {
        const err = typeof d.error === 'string' ? d.error : 'Inference job failed';
        throw new Error(err);
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error('Inference job timed out');
  }

  const loginWithCognito = () => {
    if (!cognitoDomain || !cognitoClientId) return;
    const base = cognitoDomain.startsWith('http') ? cognitoDomain : `https://${cognitoDomain}`;
    const url = new URL('/login', base);
    url.searchParams.set('client_id', cognitoClientId);
    url.searchParams.set('response_type', cognitoResponseType);
    url.searchParams.set('scope', cognitoScope);
    url.searchParams.set('redirect_uri', cognitoRedirectUri);
    window.location.href = url.toString();
  };

  const logout = () => {
    try {
      localStorage.removeItem('AUTH_ACCESS_TOKEN');
      localStorage.removeItem('access_token');
      localStorage.removeItem('id_token');
    } catch {}
    setIsAuthenticated(false);
    if (!cognitoDomain || !cognitoClientId) return;
    const base = cognitoDomain.startsWith('http') ? cognitoDomain : `https://${cognitoDomain}`;
    const url = new URL('/logout', base);
    url.searchParams.set('client_id', cognitoClientId);
    url.searchParams.set('logout_uri', cognitoLogoutUri);
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
        role:
          t.role === "staff" ? "customer" : t.role === "customer" ? "staff" : t.role,
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

  async function openWs() {
    closeWs();
    const myAttempt = streamAttemptRef.current;

    // Fresh speaker map for each listening session.
    speakerRoleMapRef.current = {};
    nextVoiceIsStaffRef.current = true;
    setSpeakerRoleMap({});
    setNextVoiceIsStaff(true);
    setLiveRole('unknown');

    const media = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (myAttempt !== streamAttemptRef.current) {
      media.getTracks().forEach((t) => t.stop());
      return;
    }
    mediaRef.current = media;
    const AudioContextCls = (window as any).AudioContext || (window as any).webkitAudioContext;
    const ctx = new AudioContextCls();
    audioCtxRef.current = ctx;
    const source = ctx.createMediaStreamSource(media);
    mediaSourceRef.current = source;
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    processorRef.current = proc;
    source.connect(proc); proc.connect(ctx.destination);
    function pcmEncode(input: Float32Array) {
      const out = new Int16Array(input.length);
      for (let i=0;i<input.length;i++) out[i] = Math.max(-1, Math.min(1, input[i])) * 0x7fff;
      return out.buffer;
    }
    
    let streamingToken = '';
    let keytermsPrompt: string[] = [];
    try {
      const res = await fetch(`${backendUrl}/assemblyai-token`, {
        headers: getAuthHeaders(),
      });
      if (!res.ok) {
        alert('Failed to obtain streaming token from backend.');
        closeWs();
        return;
      }
      const payload = await res.json();
      streamingToken = String(payload.token || '').trim();
      keytermsPrompt = Array.isArray(payload.keyterms_prompt)
        ? payload.keyterms_prompt.map((t: unknown) => String(t))
        : [];
      if (!streamingToken) {
        alert('Backend returned empty streaming token.');
        closeWs();
        return;
      }
    } catch (err) {
      console.error('[Frontend] Failed to obtain streaming token:', err);
      alert('Unable to obtain streaming token. Check backend.');
      closeWs();
      return;
    }

    // Connect directly to AssemblyAI WebSocket v3 with server-issued temporary token.
    // speaker_labels + max_speakers enable real-time diarization for staff/customer.
    const params = new URLSearchParams({
      sample_rate: String(ctx.sampleRate || 48000),
      format_turns: 'true',
      speaker_labels: 'true',
      max_speakers: '2',
      token: streamingToken,
    });
    if (keytermsPrompt.length > 0) {
      params.set('keyterms_prompt', JSON.stringify(keytermsPrompt));
    }
    const ws = new WebSocket(`wss://streaming.assemblyai.com/v3/ws?${params}`);
    if (myAttempt !== streamAttemptRef.current) {
      try { ws.close(); } catch {}
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
      try { socket.send(pcm); } catch {}
    };
    
    ws.onmessage = (evt) => {
      try {
        const d = JSON.parse(evt.data as string) as Record<string, unknown>;
        const text: string = String(d.transcript || d.text || '');
        if (!text) return;

        const speakerLabel = extractSpeakerLabel(d);
        
        // Robust final/partial detection (supports AssemblyAI message_type and type)
        const mt = String(d.message_type || '').toLowerCase();
        const tt = String(d.type || '').toLowerCase();
        const isFinal = (
          d.end_of_turn === true ||
          String(d.end_of_turn).toLowerCase() === 'true' ||
          tt.includes('final') ||
          mt.includes('final') ||
          mt === 'transcript_complete'
        );
        
        // Helper function to normalize text for duplicate comparison
        const normalizeForCompare = (s: string): string => {
          return s
            .toLowerCase()
            .replace(/[^\w\s]/g, '') // Remove punctuation
            .replace(/\s+/g, ' ')     // Normalize whitespace
            .trim();
        };
        
        // Helper function to normalize a word (convert numbers to a standard form for comparison)
        const normalizeWord = (word: string): string => {
          // Convert number words to digits for comparison
          const numberWords: { [key: string]: string } = {
            'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
            'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
            'ten': '10', 'eleven': '11', 'twelve': '12', 'thirteen': '13',
            'fourteen': '14', 'fifteen': '15', 'sixteen': '16', 'seventeen': '17',
            'eighteen': '18', 'nineteen': '19', 'twenty': '20'
          };
          const lower = word.toLowerCase();
          if (numberWords[lower]) {
            return numberWords[lower];
          }
          // If it's already a digit, keep it as is
          if (/^\d+$/.test(word)) {
            return word;
          }
          return lower;
        };
        
        // Helper function to check if two texts are similar enough to be considered duplicates
        const areSimilar = (text1: string, text2: string): boolean => {
          if (!text1 || !text2) return false;
          
          const norm1 = normalizeForCompare(text1);
          const norm2 = normalizeForCompare(text2);
          
          // Exact match after normalization
          if (norm1 === norm2) return true;
          
          // Tokenize into words
          const words1 = norm1.split(/\s+/).filter(w => w.length > 0).map(normalizeWord);
          const words2 = norm2.split(/\s+/).filter(w => w.length > 0).map(normalizeWord);
          
          // If word counts are very different, not similar
          if (Math.abs(words1.length - words2.length) > 2) return false;
          
          // Check overlap: count how many words match (after number normalization)
          const set1 = new Set(words1);
          const set2 = new Set(words2);
          
          // Count matches
          let matches = 0;
          for (const word of set1) {
            if (set2.has(word)) matches++;
          }
          
          // Consider similar if most words match (at least 80% of unique words)
          const minWords = Math.min(set1.size, set2.size);
          if (minWords === 0) return false;
          const similarity = matches / minWords;
          
          return similarity >= 0.8;
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
        
        // REAL-TIME DISPLAY
        if (isFinal) {
          const trimmed = text.trim();
          if (!trimmed) return;
          const normalizedNew = normalizeForCompare(trimmed);
          const normalizedLive = normalizeForCompare(liveRef.current || '');
          let role = assignRole(speakerLabel);
          
          setTurns(prev => {
            if (prev.length === 0) {
              return [{
                id: newTurnId(),
                text: trimmed,
                speakerLabel,
                role,
              }];
            }
            const last = prev[prev.length - 1];
            const normalizedLast = normalizeForCompare(last.text);
            
            // If final matches current live or last final (normalized or similar), replace last turn
            if (
              normalizedNew === normalizedLive ||
              normalizedNew === normalizedLast ||
              areSimilar(trimmed, liveRef.current || '') ||
              areSimilar(trimmed, last.text)
            ) {
              const mergedLabel = speakerLabel || last.speakerLabel;
              let mergedRole = role;
              if (mergedRole === "unknown" && mergedLabel) {
                mergedRole = speakerRoleMapRef.current[mergedLabel]
                  || last.role
                  || "unknown";
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
            
            // Otherwise append as a new turn
            return [
              ...prev,
              {
                id: newTurnId(),
                text: trimmed,
                speakerLabel,
                role,
              },
            ];
          });
          
          // Clear live
          setLive('');
          liveRef.current = '';
          setLiveRole('unknown');
        } else {
          // Always show partial immediately (no gating)
          const trimmed = text.trim();
          setLive(trimmed);
          liveRef.current = trimmed;
          setLiveRole(assignRole(speakerLabel));
        }
      } catch (err) {
        console.error('[Frontend] WebSocket message error:', err);
      }
    };
  }

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
      try { ws.close(); } catch {}
    }

    const proc = processorRef.current;
    processorRef.current = null;
    if (proc) {
      try { proc.onaudioprocess = null; } catch {}
      try { proc.disconnect(); } catch {}
    }

    try { mediaSourceRef.current?.disconnect(); } catch {}
    mediaSourceRef.current = null;

    const ctx = audioCtxRef.current;
    audioCtxRef.current = null;
    try { ctx?.close(); } catch {}

    try { mediaRef.current?.getTracks().forEach((t) => t.stop()); } catch {}
    mediaRef.current = null;

    setLive('');
    liveRef.current = '';
    setLiveRole('unknown');
    setIsListening(false);
  }

  // Handler for manual customer data field changes
  const handleCustomerDataChange = (field: CustomerDataFields, value: string) => {
    setCustomerData(prev => ({ ...prev, [field]: value }));
    setManuallyEditedFields(prev => new Set(prev).add(field));
  };

  // Extract customer data from transcript
  const extractCustomerData = useCallback(async (context: string) => {
    if (!context || context.trim().length < 10) {
      return;
    }
    
    // Prevent redundant extractions
    const normalizedCurrent = context.trim().toLowerCase();
    const normalizedLast = lastCustomerDataExtractRef.current.trim().toLowerCase();
    
    if (normalizedCurrent === normalizedLast) {
      return;
    }
    
    try {
      lastCustomerDataExtractRef.current = context;

      let data: Record<string, unknown>;

      if (useAsyncInferenceJobs) {
        const er = await fetch(`${backendUrl}/queue/extract-customer-data`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
          body: JSON.stringify({
            conversation_transcript: context,
            session_id: sessionIdRef.current || undefined,
          }),
        });
        const ej = await er.json();
        if (!er.ok || typeof ej.job_id !== 'string') {
          console.error('[Frontend] extract enqueue failed:', er.status);
          return;
        }
        data = await pollInferenceJob(ej.job_id as string);
      } else {
        const res = await fetch(`${backendUrl}/extract-customer-data`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
          body: JSON.stringify({
            conversation_transcript: context,
            session_id: sessionIdRef.current || undefined,
          }),
        });
        if (!res.ok) return;
        data = await res.json();
      }

      const successVal = Boolean(data.success);
      const rawData = data.data && typeof data.data === 'object' ? (data.data as Record<string, unknown>) : null;
      if (successVal && rawData) {
        setCustomerData((prev) => {
          const updated = { ...prev };
          const currentEditedFields = manuallyEditedFieldsRef.current;
          Object.keys(rawData).forEach((key) => {
            const field = key as CustomerDataFields;
            const extractedValue = rawData[field];
            if (extractedValue == null || extractedValue === '') return;
            if (!currentEditedFields.has(field)) {
              updated[field] = String(extractedValue as string);
            }
          });
          return updated;
        });
      }
    } catch (err) {
      console.error('[Frontend] Failed to extract customer data:', err);
    }
  }, [backendUrl, useAsyncInferenceJobs]);

  // Ref to track the last transcript we sent to avoid redundant requests
  const lastTranscriptRef = useRef<string>('');
  // Ref to track the last time we fetched suggestions
  const lastFetchTimeRef = useRef<number>(0);
  // Ref to store the debounce timeout
  const debounceTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  
  const fetchSuggestions = async (context: string) => {
    if (!context || context.trim().length < 10) {
      setSuggestions([]);
      return;
    }
    
    // Prevent redundant fetches if transcript hasn't changed
    const normalizedCurrent = context.trim().toLowerCase();
    const normalizedLast = lastTranscriptRef.current.trim().toLowerCase();
    
    if (normalizedCurrent === normalizedLast) {
      console.log('[Frontend] Transcript unchanged, skipping suggestion fetch');
      return;
    }
    
    try {
      console.log(`[Frontend] Fetching suggestions for transcript (${context.length} chars): "${context.substring(0, 100)}..."`);
      lastTranscriptRef.current = context;
      lastFetchTimeRef.current = Date.now();

      if (useAsyncInferenceJobs) {
        const er = await fetch(`${backendUrl}/queue/suggestions`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
          body: JSON.stringify({
            context,
            max_suggestions: 2,
            session_id: sessionIdRef.current || undefined,
          }),
        });
        const ej = await er.json();
        if (!er.ok || typeof ej.job_id !== 'string') {
          console.error('[Frontend] Suggestion enqueue failed:', er.status);
          return;
        }
        const result = await pollInferenceJob(ej.job_id as string);
        const sug = Array.isArray((result as Record<string, unknown>).suggestions)
          ? (result as Record<string, unknown>).suggestions
          : [];
        console.log(`[Frontend] Received ${(sug as unknown[]).length} suggestions`);
        setSuggestions(sug as Suggestion[]);
      } else {
        const res = await fetch(`${backendUrl}/suggest`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
          body: JSON.stringify({
            context,
            max_suggestions: 2,
            session_id: sessionIdRef.current || undefined,
          }),
        });
        if (res.ok) {
          const data = await res.json();
          console.log(`[Frontend] Received ${data.suggestions?.length || 0} suggestions`);
          setSuggestions(data.suggestions || []);
        } else {
          console.error(`[Frontend] Suggestion request failed: ${res.status}`);
        }
      }
    } catch (err) {
      console.error('[Frontend] Failed to fetch suggestions:', err);
      // Don't clear suggestions on error - keep last ones
    }
  };

  const obtainCustomerInfo = useCallback(async () => {
    if (isLoadingCustomerHistory) return;
    setIsLoadingCustomerHistory(true);
    try {
      // Simulate a database lookup for customer profile history.
      await new Promise((resolve) => setTimeout(resolve, 700));
      setCustomerHistory(
        "Raja has had previous employer dispute cases. Please refer to Case #CH298D for more information."
      );
    } finally {
      setIsLoadingCustomerHistory(false);
    }
  }, [isLoadingCustomerHistory]);

  useEffect(() => {
    // Only request suggestions and extract customer data if there's meaningful transcript content
    if (!transcriptText || transcriptText.trim().length < 10) {
      setSuggestions([]);
      lastTranscriptRef.current = '';
      lastCustomerDataExtractRef.current = '';
      // Clear any pending debounce
      if (debounceTimeoutRef.current) {
        clearTimeout(debounceTimeoutRef.current);
        debounceTimeoutRef.current = null;
      }
      return;
    }
    
    // Clear any existing debounce timeout
    if (debounceTimeoutRef.current) {
      clearTimeout(debounceTimeoutRef.current);
    }
    
    // Debounce: Wait 1.5 seconds after transcript stops changing before fetching
    // This ensures we only fetch when the user has finished speaking (final turns have stabilized)
    debounceTimeoutRef.current = setTimeout(() => {
      // Check again if transcript has changed during the debounce period
      const normalizedCurrent = transcriptText.trim().toLowerCase();
      const normalizedLast = lastTranscriptRef.current.trim().toLowerCase();
      
      // Only fetch if transcript has actually changed
      if (normalizedCurrent !== normalizedLast) {
        console.log('[Frontend] Transcript stabilized, fetching suggestions and extracting customer data...');
        fetchSuggestions(transcriptText);
        extractCustomerData(transcriptText);
      }
      debounceTimeoutRef.current = null;
    }, 1500); // 1.5 second debounce - waits for conversation to stabilize
    
    return () => {
      if (debounceTimeoutRef.current) {
        clearTimeout(debounceTimeoutRef.current);
        debounceTimeoutRef.current = null;
      }
    };
  }, [backendUrl, transcriptText, extractCustomerData]);

  const mappedStaffLabel = Object.entries(speakerRoleMap).find(([, r]) => r === "staff")?.[0];
  const mappedCustomerLabel = Object.entries(speakerRoleMap).find(([, r]) => r === "customer")?.[0];
  const hasRoleMapping = Object.keys(speakerRoleMap).length > 0;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-brand">
          <span className="topbar-title">Conversation intelligence</span>
          <span className="topbar-sub">Live transcription and operator guidance</span>
        </div>
        <div className="topbar-actions">
          {cognitoDomain && cognitoClientId ? (
            isAuthenticated ? (
              <button type="button" className="btn btn--ghost" onClick={logout}>
                Logout
              </button>
            ) : (
              <button type="button" className="btn btn--ghost" onClick={loginWithCognito}>
                Login
              </button>
            )
          ) : null}
          <span
            className={`status-pill${isListening ? " status-pill--live" : ""}`}
            aria-live="polite"
          >
            <span className="status-pill__dot" aria-hidden />
            {isListening ? "Listening" : "Idle"}
          </span>
          <button type="button" className="btn btn--primary" onClick={() => openWs()}>
            Start session
          </button>
          <button type="button" className="btn btn--ghost" onClick={() => closeWs()}>
            Stop
          </button>
        </div>
      </header>

      <main className="layout-main">
        <section className="panel" aria-label="Live transcript">
          <div className="panel-header panel-header--compact">
            <h1 className="panel-title">Live conversation</h1>
            <p className="panel-hint panel-hint--tight">Speaker-labeled turns from the laptop mic</p>
          </div>

          <div className="diarization-controls" role="group" aria-label="Speaker role controls">
            <span className="diarization-controls__label">Speakers</span>
            <div className="diarization-controls__buttons">
              <button
                type="button"
                className={`btn btn--ghost btn--compact${nextVoiceIsStaff ? " btn--active" : ""}`}
                onClick={() => setNextVoiceRole(true)}
                aria-pressed={nextVoiceIsStaff}
                disabled={hasRoleMapping && Boolean(mappedStaffLabel)}
                title={
                  mappedStaffLabel
                    ? `Staff mapped to speaker ${mappedStaffLabel}`
                    : "Lock the next unseen speaker as Staff"
                }
              >
                Staff
              </button>
              <button
                type="button"
                className={`btn btn--ghost btn--compact${!nextVoiceIsStaff ? " btn--active" : ""}`}
                onClick={() => setNextVoiceRole(false)}
                aria-pressed={!nextVoiceIsStaff}
                disabled={hasRoleMapping && Boolean(mappedCustomerLabel)}
                title={
                  mappedCustomerLabel
                    ? `Customer mapped to speaker ${mappedCustomerLabel}`
                    : "Lock the next unseen speaker as Customer"
                }
              >
                Customer
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--compact"
                onClick={swapSpeakerRoles}
                disabled={!hasRoleMapping}
                title="Swap Staff and Customer labels if diarization inverted them"
              >
                Swap
              </button>
            </div>
            {hasRoleMapping ? (
              <span className="diarization-controls__map" aria-live="polite">
                {mappedStaffLabel ? `Staff ← ${mappedStaffLabel}` : "Staff ← —"}
                {" · "}
                {mappedCustomerLabel ? `Customer ← ${mappedCustomerLabel}` : "Customer ← —"}
              </span>
            ) : (
              <span className="diarization-controls__map diarization-controls__map--muted">
                Waiting for first speaker…
              </span>
            )}
          </div>

          <div className="transcript-list" ref={transcriptListRef}>
            {turns.length === 0 && !live && (
              <p className="empty-state">Start a session and speak to see the transcript here.</p>
            )}
            {turns.map((t) => (
              <div
                key={t.id}
                className={`bubble bubble--${t.role}`}
              >
                <span className={`bubble-badge bubble-badge--${t.role}`}>
                  {roleDisplayName(t.role)}
                </span>
                <span className="bubble-text">{t.text}</span>
              </div>
            ))}
            {live ? (
              <div className={`bubble bubble--live bubble--${liveRole}`}>
                <span className={`bubble-badge bubble-badge--${liveRole}`}>
                  {roleDisplayName(liveRole)}
                </span>
                <span className="bubble-text">{live}</span>
              </div>
            ) : null}
          </div>
        </section>

        <aside className="panel sidebar" aria-label="AI suggestions and customer data">
          <div className="sidebar-block sidebar-block--suggestions">
            <div className="panel-header panel-header--compact">
              <h2 className="panel-title">AI suggestions</h2>
              <p className="panel-hint panel-hint--tight">What to say next</p>
            </div>

            <div className="suggestions-scroll">
              {suggestions.length === 0 ? (
                <p className="empty-state">
                  {transcriptText.trim().length < 10
                    ? "Suggestions appear after there is enough transcript to analyze."
                    : "Analyzing the latest transcript…"}
                </p>
              ) : (
                suggestions.map((s, i) => {
                  const details = s.details || {};
                  const topic = s.topic || s.text || "Follow up on conversation";
                  const possibleConversation = details.possibleConversation || "";

                  return (
                    <article key={i} className="suggestion-card">
                      <span className="suggestion-badge">{s.type || "Suggestion"}</span>

                      <div>
                        <div className="suggestion-block-title">Topic / context</div>
                        <p className="suggestion-topic">{topic}</p>
                      </div>

                      {possibleConversation ? (
                        <div className="suggestion-followup">
                          <div className="suggestion-block-title">Possible phrasing</div>
                          <p className="suggestion-quote">{possibleConversation}</p>
                        </div>
                      ) : null}
                    </article>
                  );
                })
              )}
            </div>
          </div>

          <div className="divider" />

          <div className="sidebar-block sidebar-block--customer">
            <div className="panel-header panel-header--compact">
              <div>
                <h2 className="panel-title">Customer data</h2>
                <p className="panel-hint panel-hint--tight">
                  Auto-filled from the call. Your edits are kept.
                </p>
              </div>
            </div>

            <div className="customer-scroll">
              <div className="field-grid">
                <div className="field-group">
                  <label className="field-label" htmlFor="cust-name">
                    Name
                  </label>
                  <input
                    id="cust-name"
                    className="field-input"
                    type="text"
                    value={customerData.name}
                    onChange={(e) => handleCustomerDataChange("name", e.target.value)}
                    placeholder="Customer name"
                    autoComplete="off"
                  />
                </div>

                <div className="field-group">
                  <label className="field-label" htmlFor="cust-id">
                    NRIC / Work Permit ID
                  </label>
                  <input
                    id="cust-id"
                    className="field-input"
                    type="text"
                    value={customerData.nric_worker_permit_id}
                    onChange={(e) => handleCustomerDataChange("nric_worker_permit_id", e.target.value)}
                    placeholder="e.g. S1234567A"
                    autoComplete="off"
                  />
                </div>
              </div>

              <div className="field-group">
                <label className="field-label" htmlFor="cust-address">
                  Address
                </label>
                <textarea
                  id="cust-address"
                  className="field-textarea"
                  value={customerData.address}
                  onChange={(e) => handleCustomerDataChange("address", e.target.value)}
                  placeholder="Customer address"
                  rows={2}
                />
              </div>

              <div className="field-group">
                <label className="field-label" htmlFor="cust-purpose">
                  Purpose of call
                </label>
                <textarea
                  id="cust-purpose"
                  className="field-textarea"
                  value={customerData.purpose_of_call}
                  onChange={(e) => handleCustomerDataChange("purpose_of_call", e.target.value)}
                  placeholder="Reason for the call"
                  rows={2}
                />
              </div>

              <div className="customer-info-actions">
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={obtainCustomerInfo}
                  disabled={isLoadingCustomerHistory}
                >
                  {isLoadingCustomerHistory ? "Obtaining..." : "Obtain customer info"}
                </button>
              </div>

              {customerHistory ? (
                <section className="customer-history" aria-live="polite">
                  <div className="customer-history__title">Customer history</div>
                  <p className="customer-history__text">{customerHistory}</p>
                  <div className="customer-history__table-wrap">
                    <table className="customer-history__table" aria-label="Customer case summary">
                      <thead>
                        <tr>
                          <th>Case ID</th>
                          <th>Company</th>
                          <th>Type</th>
                          <th>Status</th>
                          <th>Summary</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td>#CH298D</td>
                          <td>ABC</td>
                          <td>Employer dispute</td>
                          <td>Resolved</td>
                          <td>Salary underpayment complaint settled through mediation.</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </section>
              ) : null}
            </div>
          </div>
        </aside>
      </main>
    </div>
  );
}
