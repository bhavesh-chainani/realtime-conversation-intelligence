import { memo } from "react";

import { isOpenCaseStatus } from "../lib/cases.ts";
import type { CustomerHistoryCase, Suggestion, SuggestionMeta } from "../lib/types.ts";

type SuggestionsPanelProps = {
  suggestions: Suggestion[];
  meta: SuggestionMeta | null;
  cases: CustomerHistoryCase[];
  hasTranscript: boolean;
  isLive: boolean;
  isFetchingSuggestions: boolean;
};

function formatSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

function latencyLabel(meta: SuggestionMeta): string {
  if (meta.origin === "fallback") return "Fallback guidance";
  const parts = [
    meta.turnEndMs >= 50 ? `turn end ${formatSeconds(meta.turnEndMs)}` : "",
    meta.speakerMs >= 50 ? `speaker ${formatSeconds(meta.speakerMs)}` : "",
    typeof meta.llmMs === "number" ? `AI ${formatSeconds(meta.llmMs)}` : "",
  ].filter(Boolean);
  return `Live · ${formatSeconds(meta.latencyMs)}${parts.length ? ` (${parts.join(" · ")})` : ""}`;
}

/** The cases a suggestion relies on, with status; open cases are highlighted. */
function RecordChips({ ids, cases }: { ids: string[]; cases: CustomerHistoryCase[] }) {
  // The backend only keeps IDs that are in the record, so every chip has a case.
  const shown = ids
    .map((id) => cases.find((c) => c.case_id === id))
    .filter((c): c is CustomerHistoryCase => c !== undefined);
  if (shown.length === 0) return null;
  return (
    <div className="record-links" aria-label="Based on">
      <span className="record-links__label">Based on</span>
      {shown.map((c) => (
        <span
          key={c.case_id}
          className={`record-chip${isOpenCaseStatus(c.status) ? " record-chip--open" : ""}`}
          title={`${c.type} · ${c.company} · ${c.summary}`}
        >
          {c.case_id}
          <span className="record-chip__status">{c.status}</span>
        </span>
      ))}
    </div>
  );
}

/** Memoised: the page re-renders on every mic-level update, this panel only when its inputs change. */
export const SuggestionsPanel = memo(function SuggestionsPanel({
  suggestions,
  meta,
  cases,
  hasTranscript,
  isLive,
  isFetchingSuggestions,
}: SuggestionsPanelProps) {
  let emptyMessage = "Guidance appears here when the customer speaks.";
  if (hasTranscript && isFetchingSuggestions) emptyMessage = "Preparing a suggestion…";
  else if (isLive) emptyMessage = "Listening for the customer…";

  return (
    <section className="rail-section suggestion-hero" aria-label="Suggested response">
      <div className="panel-heading">
        <h2 className="panel-title">Suggested response</h2>
        {meta ? (
          <span
            className={`latency-chip latency-chip--${meta.origin}${isFetchingSuggestions ? " latency-chip--busy" : ""}`}
            title={
              meta.model
                ? `Time from end of customer turn to suggestion · ${meta.model}`
                : "Time from end of customer turn to suggestion"
            }
          >
            {latencyLabel(meta)}
          </span>
        ) : null}
      </div>

      {suggestions.length === 0 ? (
        <div className={`hero-empty${isLive || isFetchingSuggestions ? " hero-empty--active" : ""}`}>
          <span className="hero-empty__dot" aria-hidden />
          {emptyMessage}
        </div>
      ) : (
        suggestions.map((suggestion) => {
          const details = suggestion.details || {};
          const topic = suggestion.topic || "";
          const phrasing = details.possibleConversation || "";
          const confidence =
            typeof suggestion.confidence === "number" && Number.isFinite(suggestion.confidence)
              ? `${Math.round(suggestion.confidence * 100)}% confidence`
              : "";

          return (
            <article
              // Content-based key replays the entrance animation whenever the guidance changes.
              key={`${topic}-${phrasing.slice(0, 40)}`}
              className={`hero-card${String(details.priority).toLowerCase() === "high" ? " hero-card--priority" : ""}`}
            >
              <div className="hero-card__tags">
                {suggestion.type ? <span className="tag tag--accent">{suggestion.type}</span> : null}
                {details.priority ? <span className="tag tag--muted">Priority: {String(details.priority)}</span> : null}
                {confidence ? <span className="tag tag--muted">{confidence}</span> : null}
              </div>
              <h3 className="hero-card__topic">{topic}</h3>
              {phrasing ? <p className="hero-card__quote">“{phrasing}”</p> : null}
              <RecordChips ids={suggestion.linked_records || []} cases={cases} />
            </article>
          );
        })
      )}
    </section>
  );
});
