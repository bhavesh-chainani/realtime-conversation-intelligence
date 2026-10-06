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
import { parseHistoryResult, streamAssist } from "../lib/assist-stream.ts";
import { obj, postJson } from "../lib/parse.ts";
import type { CustomerDataField, Turn } from "../lib/types.ts";

export function useAssist(backendUrl: string) {
  const [state, setState] = useState<AssistState>(initialAssistState);
  // The reducer runs on this ref so async callbacks always see (and build on) the latest state.
  const stateRef = useRef(state);
  const dispatch = useCallback((action: AssistAction) => {
    stateRef.current = assistReducer(stateRef.current, action);
    setState(stateRef.current);
  }, []);

  const abortRef = useRef<AbortController | null>(null);
  const lastTurnRef = useRef<{ turn: Turn; turns: Turn[] } | null>(null);
  const endedRef = useRef(false);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  /** Ask both agents about `turn`. A newer call aborts this one (and its LLM calls on the server). */
  const run = useCallback(
    (turn: Turn, turns: Turn[], extract = true) => {
      lastTurnRef.current = { turn, turns };
      cancel();
      const controller = new AbortController();
      abortRef.current = controller;
      const gen = stateRef.current.gen + 1;
      dispatch({ type: "streamStart", gen, committedAt: turn.committedAt, endedAt: turn.endedAt });
      const body = buildAssistRequest(stateRef.current, turns, extract);
      streamAssist(backendUrl, body, controller.signal, (event) => {
        if (event.type === "error") console.warn(`[assist] ${event.stage} failed: ${event.message}`);
        dispatch({ type: "event", gen, event, at: performance.now() });
      })
        .catch((err) => {
          if (!controller.signal.aborted) console.error("[assist] stream failed:", err);
        })
        .finally(() => dispatch({ type: "streamEnd", gen }));
    },
    [backendUrl, cancel, dispatch]
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

  return { ...state, run, editField, lookup, end, reset };
}
