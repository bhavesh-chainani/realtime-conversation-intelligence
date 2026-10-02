import { useState } from "react";

import type { CustomerHistoryCase, Wrapup, WrapupState } from "../lib/types.ts";
import { RecordChips } from "./suggestions-panel";

type WrapupCardProps = {
  state: WrapupState;
  cases: CustomerHistoryCase[];
  techView: boolean;
  /** Return to the live view (End call pressed by mistake, or to keep talking). */
  onBackToCall: () => void;
};

function notesText(w: Wrapup): string {
  const lines = [`Summary: ${w.summary}`];
  if (w.issue) lines.push(`Issue: ${w.issue}`);
  if (w.linked_records?.length) lines.push(`Linked cases: ${w.linked_records.join(", ")}`);
  if (w.actions?.length) lines.push("Actions:", ...w.actions.map((a) => `- ${a}`));
  if (w.documents_requested?.length) lines.push("Documents requested:", ...w.documents_requested.map((d) => `- ${d}`));
  if (w.follow_up) lines.push(`Follow-up: ${w.follow_up}`);
  return lines.join("\n");
}

export function WrapupCard({ state, cases, techView, onBackToCall }: WrapupCardProps) {
  const [copied, setCopied] = useState(false);
  const data = state.data;

  const copy = async () => {
    if (!data) return;
    try {
      await navigator.clipboard.writeText(notesText(data));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <section className="rail-section suggestion-hero" aria-label="Call wrap-up">
      <div className="panel-heading">
        <h2 className="panel-title">Wrap-up notes</h2>
        {techView && state.status === "ready" ? (
          <span className={`latency-chip latency-chip--${state.origin === "prepared" ? "instant" : "live"}`}>
            {state.origin === "prepared" ? "Prepared" : "Live"}
            {state.latencyMs != null ? ` · ${(state.latencyMs / 1000).toFixed(1)}s` : ""}
          </span>
        ) : null}
        <button type="button" className="btn btn--ghost btn--sm wrapup-card__back" onClick={onBackToCall}>
          ← Back to call
        </button>
        {state.status === "ready" && data ? (
          // In the header so it is always visible, even when the notes are long.
          <button type="button" className="btn btn--primary btn--sm wrapup-card__copy" onClick={() => void copy()}>
            {copied ? "Copied ✓" : "Copy to case notes"}
          </button>
        ) : null}
      </div>

      {state.status === "loading" || state.status === "idle" ? (
        <div className="hero-empty hero-empty--active">
          <span className="hero-empty__dot" aria-hidden />
          Drafting case notes from the call…
        </div>
      ) : null}

      {state.status === "error" ? (
        <div className="hero-empty">Couldn&apos;t draft notes for this call. Add notes manually in the case system.</div>
      ) : null}

      {state.status === "ready" && data ? (
        <article className="hero-card wrapup-card">
          {data.issue ? <h3 className="hero-card__topic">{data.issue}</h3> : null}
          <p className="wrapup-card__summary">{data.summary}</p>
          <RecordChips ids={data.linked_records || []} cases={cases} label="Linked cases" />

          {data.actions?.length ? (
            <div className="wrapup-card__block">
              <div className="wrapup-card__label">Next steps</div>
              <ul className="wrapup-card__list wrapup-card__list--check">
                {data.actions.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {data.documents_requested?.length ? (
            <div className="wrapup-card__block">
              <div className="wrapup-card__label">Documents requested</div>
              <ul className="wrapup-card__list">
                {data.documents_requested.map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {data.follow_up ? <p className="wrapup-card__follow">{data.follow_up}</p> : null}
        </article>
      ) : null}
    </section>
  );
}
