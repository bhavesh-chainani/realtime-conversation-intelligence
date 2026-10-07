// Scripted transcript for screen-recorded demos (`/?demo`): types out a fixed call in place of the mic and STT relay.
// Same shape as useLiveTranscript, so the page and panels are unchanged; /assist, wrap-up and Save stay live.
import { useCallback, useEffect, useRef, useState } from "react";

import {
  DEMO_ROLE_MAP,
  lineTimings,
  readingDwellMs,
  resolveScript,
  revealedText,
  scriptTurn,
  wordsOf,
} from "../lib/demo-playback.ts";
import type { DemoLine } from "../lib/demo-script.ts";
import type { PendingTurn } from "../lib/stt-relay.ts";
import { otherRole, swapRoles, type KnownRole, type SpeakerRoleMap } from "../lib/transcript.ts";
import type { Turn } from "../lib/types.ts";
import type { TranscriptHandlers } from "./useLiveTranscript.ts";

/** Thrown inside the playback loop when close() or reset() ends it. */
const CANCELLED = Symbol("cancelled");
const TICK_MS = 50;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function useScriptedTranscript(
  script: DemoLine[] | null,
  speed: number,
  handlers: TranscriptHandlers & { isAssistBusy: () => boolean }
) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [live, setLive] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const [pendingTurns, setPendingTurns] = useState<PendingTurn[]>([]);
  const [speakerRoleMap, setSpeakerRoleMap] = useState<SpeakerRoleMap>({});
  const [isListening, setIsListening] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [micLevel, setMicLevel] = useState(0);

  const turnsRef = useRef<Turn[]>([]);
  const roleMapRef = useRef<SpeakerRoleMap>({});
  /** Next script line to play; kept across pauses, cleared by reset. */
  const cursorRef = useRef(0);
  const pausedRef = useRef(false);
  /** Bumped by close() / reset(): a loop started under an older run stops at its next step. */
  const runRef = useRef(0);
  const playingRef = useRef(false);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const commitTurns = (next: Turn[]) => {
    turnsRef.current = next;
    setTurns(next);
  };
  const setRoleMap = (map: SpeakerRoleMap) => {
    roleMapRef.current = map;
    setSpeakerRoleMap(map);
  };
  const clearLive = () => {
    setSpeaking(false);
    setLive("");
    setPendingTurns([]);
    setMicLevel(0);
  };

  const play = useCallback(async () => {
    if (!script || playingRef.current) return;
    playingRef.current = true;
    const lines = resolveScript(script, new Date());
    const run = runRef.current;
    const timings = lineTimings(speed);
    const check = () => {
      if (run !== runRef.current) throw CANCELLED;
    };
    const untilResumed = async () => {
      while (pausedRef.current) {
        await sleep(TICK_MS);
        check();
      }
    };
    /** Sleep that stops the clock while on hold. */
    const wait = async (ms: number) => {
      for (let left = ms; left > 0; left -= TICK_MS) {
        await untilResumed();
        await sleep(Math.min(left, TICK_MS));
        check();
      }
    };
    /** Wait for the suggestion: /assist starts on the next render, so give it a moment to report busy. */
    const untilAssisted = async () => {
      await wait(250);
      while (handlersRef.current.isAssistBusy()) await wait(TICK_MS);
    };
    const hold = () => {
      pausedRef.current = true;
      setIsListening(false);
    };

    try {
      setIsConnecting(true);
      await sleep(600);
      check();
      setIsConnecting(false);
      setIsListening(!pausedRef.current);

      while (cursorRef.current < lines.length) {
        const line = lines[cursorRef.current];
        const previous = turnsRef.current[turnsRef.current.length - 1];
        if (previous?.role === "customer") {
          await untilAssisted();
          await wait(readingDwellMs(speed));
        } else {
          await wait(timings.gapMs);
        }

        setSpeaking(true);
        await wait(timings.leadInMs);
        const words = wordsOf(line.text);
        for (let n = 1; n <= words.length; n++) {
          await untilResumed();
          setLive(revealedText(line.text, n));
          setMicLevel(0.3 + Math.random() * 0.4);
          await wait(timings.perWordMs);
        }
        setSpeaking(false);
        setLive("");
        setMicLevel(0);
        setPendingTurns([{ turnOrder: cursorRef.current, text: line.text }]);
        await wait(timings.identifyMs);

        setPendingTurns([]);
        const turn = scriptTurn(line, performance.now());
        const next = [...turnsRef.current, turn];
        commitTurns(next);
        if (roleMapRef.current.A === undefined) setRoleMap(DEMO_ROLE_MAP);
        cursorRef.current += 1;
        if (line.role === "customer") handlersRef.current.onCustomerTurn(turn, next);

        if (line.holdAfter || cursorRef.current === lines.length) {
          if (line.role === "customer") await untilAssisted();
          hold();
        }
      }
    } catch (err) {
      if (err !== CANCELLED) throw err;
    } finally {
      if (run === runRef.current) playingRef.current = false;
    }
  }, [script, speed]);

  /** Stops playback for good (End call, unmount). Safe to call repeatedly. */
  const close = useCallback(() => {
    runRef.current += 1;
    playingRef.current = false;
    pausedRef.current = false;
    clearLive();
    setIsListening(false);
    setIsConnecting(false);
  }, []);

  /** Start, or resume after a hold. */
  const start = useCallback(async () => {
    pausedRef.current = false;
    if (playingRef.current) setIsListening(true);
    else void play();
  }, [play]);

  /** Hold: the loop waits at its next step, and the half-typed line stays on screen. */
  const stop = useCallback(() => {
    pausedRef.current = true;
    setIsListening(false);
    setMicLevel(0);
  }, []);

  const reset = useCallback(() => {
    close();
    cursorRef.current = 0;
    commitTurns([]);
    setRoleMap({});
  }, [close]);

  const swapSpeakerRoles = useCallback(() => {
    setRoleMap(swapRoles(roleMapRef.current));
    commitTurns(turnsRef.current.map((t) => (t.role === "unknown" ? t : { ...t, role: otherRole(t.role) })));
  }, []);

  const flipTurn = useCallback((turnId: string) => {
    const current = turnsRef.current;
    const target = current.find((t) => t.id === turnId);
    if (!target) return;
    const role: KnownRole = target.role === "staff" ? "customer" : "staff";
    const updated: Turn = { ...target, role, roleSource: "manual" };
    const next = current.map((t) => (t.id === turnId ? updated : t));
    commitTurns(next);
    if (role === "customer" && current[current.length - 1]?.id === turnId) {
      handlersRef.current.onCustomerTurn(updated, next);
    }
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
    sttDropped: false,
    start,
    stop,
    close,
    reset,
    swapSpeakerRoles,
    flipTurn,
  };
}
