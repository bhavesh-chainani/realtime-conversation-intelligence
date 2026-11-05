// End of call: drafts the wrap-up (POST /wrapup), keeps staff edits, and saves it as a case (POST /cases).
import { useCallback, useEffect, useRef, useState } from "react";

import type { CustomerData, CustomerHistoryCase, Turn } from "../lib/types.ts";
import {
  buildSaveRequest,
  buildWrapUpRequest,
  parseSaveResponse,
  parseWrapUpResponse,
  type SaveResult,
  type WrapUp,
} from "../lib/wrapup.ts";
import { postJson } from "../lib/parse.ts";

export type WrapUpStatus = "idle" | "drafting" | "ready" | "error";

type DraftInput = {
  turns: Turn[];
  customer: CustomerData;
  history: { matchedOn: string | null; cases: CustomerHistoryCase[] };
};

export function useWrapUp(backendUrl: string) {
  const [status, setStatus] = useState<WrapUpStatus>("idle");
  const [wrapup, setWrapup] = useState<WrapUp | null>(null);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<SaveResult | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const lastInputRef = useRef<DraftInput | null>(null);

  const draft = useCallback(
    async (input: DraftInput) => {
      lastInputRef.current = input;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setStatus("drafting");
      setWrapup(null);
      setSaved(null);
      setMessage("");
      let raw: unknown;
      try {
        raw = await postJson(
          `${backendUrl}/wrapup`,
          buildWrapUpRequest(input.turns, input.customer, input.history),
          controller.signal
        );
      } catch {
        return; // aborted by a newer draft or a reset
      }
      if (controller.signal.aborted) return;
      const parsed = parseWrapUpResponse(raw);
      setWrapup(parsed.wrapup);
      setMessage(parsed.message);
      setStatus(parsed.wrapup ? "ready" : "error");
    },
    [backendUrl]
  );

  const retry = useCallback(() => {
    if (lastInputRef.current) void draft(lastInputRef.current);
  }, [draft]);

  /** Staff edits to the draft (summary, follow-up, message). */
  const edit = useCallback((update: (w: WrapUp) => WrapUp) => {
    setWrapup((w) => (w ? update(w) : w));
    setSaved(null);
  }, []);

  const save = useCallback(
    async (customer: CustomerData): Promise<SaveResult | null> => {
      if (!wrapup) return null;
      setSaving(true);
      const result = parseSaveResponse(await postJson(`${backendUrl}/cases`, buildSaveRequest(customer, wrapup)));
      const caseId = result.caseId;
      if (caseId) {
        // Saving again after an edit must update this case, not create a duplicate.
        setWrapup((w) => (w ? { ...w, case: { ...w.case, action: "update", case_id: caseId } } : w));
      }
      setSaved(result);
      setSaving(false);
      return result;
    },
    [backendUrl, wrapup]
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    lastInputRef.current = null;
    setStatus("idle");
    setWrapup(null);
    setMessage("");
    setSaving(false);
    setSaved(null);
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);

  return { status, wrapup, message, saving, saved, draft, retry, edit, save, reset };
}
