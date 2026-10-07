// Runs POST /assist for each customer turn and keeps the caller card, prior cases and suggestion.
import { useCallback, useEffect, useRef, useState } from "react";

import {
  assistReducer,
  buildAssistRequest,
  casesKey,
  initialAssistState,
  type AssistAction,
  type AssistState,
} from "../lib/assist-state.ts";
import { parseHistoryResult, streamAssist, type AssistEvent } from "../lib/assist-stream.ts";
import { obj, postJson } from "../lib/parse.ts";
import { sameAssistTurns, toAssistTurns, type AssistTurn } from "../lib/transcript.ts";
import type { CustomerDataField, Turn } from "../lib/types.ts";

/** A run started early on a pending turn's provisional speakers. Nothing reaches the screen until the finished turn
 * confirms it: its events wait in `buffered`, then go live under `gen`. */
type EarlyRun = {
  controller: AbortController;
  turns: AssistTurn[];
  buffered: AssistEvent[];
  ended: boolean;
  gen: number | null;
};

function turnTiming(turn: Turn) {
  return { committedAt: turn.committedAt, endedAt: turn.endedAt, turnEndMs: turn.turnEndMs };
}

export function useAssist(backendUrl: string) {
  const [state, setState] = useState<AssistState>(initialAssistState);
  // The reducer runs on this ref so async callbacks always see (and build on) the latest state.
  const stateRef = useRef(state);
  const dispatch = useCallback((action: AssistAction) => {
    stateRef.current = assistReducer(stateRef.current, action);
    setState(stateRef.current);
  }, []);

  const abortRef = useRef<AbortController | null>(null);
  const earlyRef = useRef<EarlyRun | null>(null);
  const lastTurnRef = useRef<{ turn: Turn; turns: Turn[] } | null>(null);
  const endedRef = useRef(false);

  const dropEarly = useCallback(() => {
    earlyRef.current?.controller.abort();
    earlyRef.current = null;
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    dropEarly();
  }, [dropEarly]);

  /** POST /assist for `turns`; returns the turns it sent. */
  const stream = useCallback(
    (
      turns: Turn[],
      extract: boolean,
      signal: AbortSignal,
      onEvent: (event: AssistEvent) => void,
      onEnd: () => void
    ) => {
      const body = buildAssistRequest(stateRef.current, turns, extract);
      streamAssist(backendUrl, body, signal, (event) => {
        if (event.type === "error") console.warn(`[assist] ${event.stage} failed: ${event.message}`);
        onEvent(event);
      })
        .catch((err) => {
          if (!signal.aborted) console.error("[assist] stream failed:", err);
        })
        .finally(onEnd);
      return body.turns;
    },
    [backendUrl]
  );

  /** Ask both agents about `turn`. A newer call aborts this one (and its LLM calls on the server). If an early run was
   * started on exactly these turns, it is kept and shown instead of starting again. */
  const run = useCallback(
    (turn: Turn, turns: Turn[], extract = true) => {
      lastTurnRef.current = { turn, turns };
      const gen = stateRef.current.gen + 1;
      const early = earlyRef.current;
      earlyRef.current = null;
      // A failed early run (ended without "done") is redone rather than kept.
      const usable = early && (!early.ended || early.buffered.some((e) => e.type === "done"));
      if (early && usable && extract && sameAssistTurns(early.turns, toAssistTurns(turns))) {
        console.debug("[assist] early start kept");
        abortRef.current?.abort();
        abortRef.current = early.controller;
        early.gen = gen;
        dispatch({ type: "streamStart", gen, ...turnTiming(turn) });
        for (const event of early.buffered) dispatch({ type: "event", gen, event, at: performance.now() });
        early.buffered = [];
        if (early.ended) dispatch({ type: "streamEnd", gen });
        return;
      }
      if (early) {
        console.debug("[assist] early start redone");
        early.controller.abort();
      }
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      dispatch({ type: "streamStart", gen, ...turnTiming(turn) });
      stream(
        turns,
        extract,
        controller.signal,
        (event) => dispatch({ type: "event", gen, event, at: performance.now() }),
        () => dispatch({ type: "streamEnd", gen })
      );
    },
    [dispatch, stream]
  );

  /** Start on a pending turn before its speakers are confirmed, so the LLM runs during the speaker wait. The run on
   * screen carries on: only `run` with matching turns replaces it. */
  const startEarly = useCallback(
    (_turn: Turn, turns: Turn[]) => {
      if (endedRef.current) return;
      dropEarly();
      const controller = new AbortController();
      const early: EarlyRun = { controller, turns: [], buffered: [], ended: false, gen: null };
      earlyRef.current = early;
      early.turns = stream(
        turns,
        true,
        controller.signal,
        (event) => {
          if (early.gen === null) early.buffered.push(event);
          else dispatch({ type: "event", gen: early.gen, event, at: performance.now() });
        },
        () => {
          early.ended = true;
          if (early.gen !== null) dispatch({ type: "streamEnd", gen: early.gen });
        }
      );
    },
    [dispatch, dropEarly, stream]
  );

  const editField = useCallback(
    (field: CustomerDataField, value: string) => dispatch({ type: "manualEdit", field, value }),
    [dispatch]
  );

  /** The caller card's Look up button. New records refresh the suggestion for the latest turn. */
  const lookup = useCallback(async () => {
    const before = casesKey(stateRef.current.history);
    const { contact_number, email } = stateRef.current.customer;
    dispatch({ type: "manualLookupStart" });
    const epoch = stateRef.current.epoch;
    const reply = await postJson(`${backendUrl}/customer-history`, {
      contact_number: contact_number.trim() || undefined,
      email: email.trim() || undefined,
    });
    const raw = reply ? obj(reply) : { status: "error", summary: "Unable to obtain customer history at the moment." };
    const prefill = raw.prefill ? (obj(raw.prefill) as Record<string, string>) : null;
    dispatch({ type: "manualLookupResult", epoch, result: parseHistoryResult(raw), prefill });

    const last = lastTurnRef.current;
    if (last && !endedRef.current && casesKey(stateRef.current.history) !== before) {
      run(last.turn, last.turns, false);
    }
  }, [backendUrl, dispatch, run]);

  /** End of call: stop any work in flight and do not start more. */
  const end = useCallback(() => {
    endedRef.current = true;
    cancel();
  }, [cancel]);

  const reset = useCallback(() => {
    cancel();
    endedRef.current = false;
    lastTurnRef.current = null;
    dispatch({ type: "reset" });
  }, [cancel, dispatch]);

  useEffect(() => cancel, [cancel]);

  return { ...state, run, startEarly, dropEarly, editField, lookup, end, reset };
}
