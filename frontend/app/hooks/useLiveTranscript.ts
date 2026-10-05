// Live transcript from the backend STT relay (AssemblyAI words + Nemotron speakers), mapped to Staff / Customer.
import { useCallback, useEffect, useRef, useState } from "react";

import { startMicCapture, type MicCapture } from "../lib/mic-capture.ts";
import {
  parsePendingTurn,
  parseRelayInfo,
  parseRelaySegments,
  relayUrl,
  type PendingTurn,
  type RelaySegment,
} from "../lib/stt-relay.ts";
import {
  newTurnId,
  otherRole,
  resolveSpeakerRole,
  swapRoles,
  type KnownRole,
  type SpeakerRoleMap,
} from "../lib/transcript.ts";
import type { Turn } from "../lib/types.ts";

/** Called once per finished turn that is not Staff (unlabelled turns may be the customer too). */
type CustomerTurnHandler = (turn: Turn, turns: Turn[]) => void;

export function useLiveTranscript(backendUrl: string, onCustomerTurn: CustomerTurnHandler) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [live, setLive] = useState("");
  const [speaking, setSpeaking] = useState(false);
  /** Finished turns the relay is still attributing to a speaker. */
  const [pendingTurns, setPendingTurns] = useState<PendingTurn[]>([]);
  const [speakerRoleMap, setSpeakerRoleMap] = useState<SpeakerRoleMap>({});
  const [isListening, setIsListening] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [micLevel, setMicLevel] = useState(0);
  /** The relay connection dropped mid-call; cleared when listening resumes. */
  const [sttDropped, setSttDropped] = useState(false);

  // Mirrors of state for the WebSocket callbacks, which outlive renders.
  const turnsRef = useRef<Turn[]>([]);
  const liveRef = useRef("");
  const pendingRef = useRef<PendingTurn[]>([]);
  const roleMapRef = useRef<SpeakerRoleMap>({});
  const nextVoiceIsStaffRef = useRef(true);
  const wsRef = useRef<WebSocket | null>(null);
  const micRef = useRef<MicCapture | null>(null);
  const attemptRef = useRef(0);
  const onCustomerTurnRef = useRef(onCustomerTurn);
  onCustomerTurnRef.current = onCustomerTurn;

  const commitTurns = (next: Turn[]) => {
    turnsRef.current = next;
    setTurns(next);
  };
  const setRoleMap = (map: SpeakerRoleMap) => {
    roleMapRef.current = map;
    setSpeakerRoleMap(map);
  };
  const setPending = (next: PendingTurn[]) => {
    pendingRef.current = next;
    setPendingTurns(next);
  };
  const clearLive = () => {
    setSpeaking(false);
    setLive("");
    liveRef.current = "";
  };

  const labelRoleFor = (label: string | null): KnownRole | null => {
    const resolved = resolveSpeakerRole(label, roleMapRef.current, nextVoiceIsStaffRef.current);
    if (resolved.map !== roleMapRef.current) setRoleMap(resolved.map);
    return resolved.role === "unknown" ? null : resolved.role;
  };

  /** Adds finished speaker segments as turns, then asks for guidance once, on the last non-Staff one.
   * `heldMs` is how long the relay held them for the diariser, so latency counts from when speech ended. */
  const ingestFinal = (segments: RelaySegment[], heldMs = 0) => {
    const now = performance.now();
    const added: Turn[] = segments
      .filter((seg) => seg.text.trim())
      .map((seg) => ({
        id: newTurnId(),
        text: seg.text.trim(),
        speakerLabel: seg.speakerLabel,
        role: labelRoleFor(seg.speakerLabel) ?? "unknown",
        roleSource: "diarization",
        committedAt: now,
        endedAt: now - heldMs,
      }));
    if (added.length === 0) return;
    const next = [...turnsRef.current, ...added];
    commitTurns(next);
    const customerTurn = [...added].reverse().find((t) => t.role !== "staff");
    if (customerTurn) onCustomerTurnRef.current(customerTurn, next);
  };

  /** Releases the mic and socket. Safe to call repeatedly. */
  const close = useCallback(() => {
    attemptRef.current += 1;
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) {
      ws.onmessage = ws.onerror = ws.onclose = null;
      try {
        ws.close();
      } catch {}
    }
    micRef.current?.stop();
    micRef.current = null;
    clearLive();
    setPending([]);
    setIsListening(false);
    setIsConnecting(false);
  }, []);

  const handleMessage = (d: Record<string, unknown>) => {
    if (d.type === "SpeechStarted") {
      // u3 models send few partials: show "speaking" straight away so the screen never looks frozen.
      setSpeaking(true);
      return;
    }
    if (d.type === "Error" || d.error) {
      // Fail loudly: a silent STT failure looks like a frozen call.
      console.error("[STT] Error", d);
      alert(`Transcription error: ${String(d.error || "unknown")}`);
      close();
      return;
    }
    const pending = parsePendingTurn(d);
    if (pending) {
      // The relay finished a turn and is identifying its speakers: move it out of the live line.
      clearLive();
      if (pending.text) setPending([...pendingRef.current, pending]);
      return;
    }
    if (d.type !== "Turn") return;
    const segments = parseRelaySegments(d);
    if (!segments) {
      // Partial text has no speaker yet: Nemotron labels a turn once it is finished.
      liveRef.current = String(d.transcript || "").trim();
      setLive(liveRef.current);
      return;
    }
    const turnOrder = typeof d.turn_order === "number" ? d.turn_order : undefined;
    setPending(pendingRef.current.filter((p) => p.turnOrder !== turnOrder));
    // The next speaker's live text may already be on screen: leave it.
    ingestFinal(segments, typeof d.held_ms === "number" ? d.held_ms : 0);
  };
  const handleMessageRef = useRef(handleMessage);
  handleMessageRef.current = handleMessage;

  /** Start (or resume) listening: relay ticket and microphone in parallel, then the socket. */
  const start = useCallback(async () => {
    close();
    const attempt = attemptRef.current;
    setIsConnecting(true);
    setSttDropped(false);
    // A new stream may assign A/B differently: forget the old label map.
    setRoleMap({});
    nextVoiceIsStaffRef.current = true;

    const session = fetch(`${backendUrl}/stt/session`).then((res) => {
      if (!res.ok) throw new Error(`STT session status ${res.status}`);
      return res.json() as Promise<Record<string, unknown>>;
    });
    session.catch(() => {});

    let mic: MicCapture;
    try {
      mic = await startMicCapture((pcm) => {
        const ws = wsRef.current;
        if (ws?.readyState === WebSocket.OPEN) ws.send(pcm);
      }, setMicLevel);
    } catch (err) {
      console.error("[mic] unavailable:", err);
      alert("Microphone unavailable. Allow microphone access and try again.");
      if (attempt === attemptRef.current) close();
      return;
    }
    if (attempt !== attemptRef.current) return mic.stop();
    micRef.current = mic;

    let url: string;
    try {
      const relay = parseRelayInfo(await session);
      if (!relay) throw new Error("empty STT session");
      url = relayUrl(backendUrl, relay, mic.sampleRate);
    } catch (err) {
      console.error("[STT] Failed to start transcription:", err);
      alert("Unable to start transcription. Check the backend.");
      if (attempt === attemptRef.current) close();
      return;
    }
    if (attempt !== attemptRef.current) return;

    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onopen = () => {
      setIsConnecting(false);
      setIsListening(true);
    };
    // close() detaches this handler first, so reaching it means the connection was lost.
    ws.onclose = (evt) => {
      console.warn("[STT] Closed", evt.code, evt.reason);
      setSttDropped(true);
      close();
    };
    ws.onmessage = (evt) => {
      try {
        handleMessageRef.current(JSON.parse(evt.data as string) as Record<string, unknown>);
      } catch (err) {
        console.error("[STT] message error:", err);
      }
    };
  }, [backendUrl, close]);

  /** Stop listening without losing the sentence that was still being transcribed. */
  const stop = useCallback(() => {
    if (wsRef.current) {
      const unfinished = [...pendingRef.current.map((p) => p.text), liveRef.current];
      ingestFinal(unfinished.map((text) => ({ text, speakerLabel: null })));
    }
    close();
  }, [close]);

  const reset = useCallback(() => {
    close();
    commitTurns([]);
    setRoleMap({});
    nextVoiceIsStaffRef.current = true;
    setSttDropped(false);
  }, [close]);

  const swapSpeakerRoles = useCallback(() => {
    setRoleMap(swapRoles(roleMapRef.current));
    commitTurns(turnsRef.current.map((t) => (t.role === "unknown" ? t : { ...t, role: otherRole(t.role) })));
    nextVoiceIsStaffRef.current = !nextVoiceIsStaffRef.current;
  }, []);

  /** Staff correction of one turn; correcting the latest turn to Customer asks for guidance on it. */
  const flipTurn = useCallback((turnId: string) => {
    const current = turnsRef.current;
    const target = current.find((t) => t.id === turnId);
    if (!target) return;
    const role: KnownRole = target.role === "staff" ? "customer" : "staff";
    const updated: Turn = { ...target, role, roleSource: "manual" };
    const next = current.map((t) => (t.id === turnId ? updated : t));
    commitTurns(next);
    if (role === "customer" && current[current.length - 1]?.id === turnId) onCustomerTurnRef.current(updated, next);
  }, []);

  useEffect(() => close, [close]);

  return {
    turns,
    live,
    speaking,
    pendingTurns,
    speakerRoleMap,
    isListening,
    isConnecting,
    micLevel,
    sttDropped,
    start,
    stop,
    close,
    reset,
    swapSpeakerRoles,
    flipTurn,
  };
}
