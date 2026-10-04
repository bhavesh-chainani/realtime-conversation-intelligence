import {
  isOpenCaseStatus,
  type CustomerHistoryCase,
  type Suggestion,
  type SuggestionMeta,
} from "../lib/types.ts";

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
  const total = meta.latencyMs != null ? formatSeconds(meta.latencyMs) : "";
  const llm = typeof meta.llmMs === "number" ? ` (LLM ${formatSeconds(meta.llmMs)})` : "";
  return `Live · ${total}${llm}`;
}

/** Case chips with status; open cases are highlighted. Shared with the wrap-up card. */
export function RecordChips({ ids, cases, label }: { ids: string[]; cases: CustomerHistoryCase[]; label: string }) {
  const caseById = new Map(cases.map((c) => [c.case_id, c]));
  const shown = ids.filter((id) => caseById.has(id) || cases.length === 0);
  if (shown.length === 0) return null;
  return (
    <div className="record-links" aria-label={label}>
      <span className="record-links__label">{label}</span>
      {shown.map((id) => {
        const c = caseById.get(id);
        const open = c ? isOpenCaseStatus(c.status) : false;
        return (
          <span
            key={id}
            className={`record-chip${open ? " record-chip--open" : ""}`}
            title={c ? `${c.type} · ${c.company} · ${c.summary}` : id}
          >
            {id}
            {c ? <span className="record-chip__status">{c.status}</span> : null}
          </span>
        );
      })}
    </div>
  );
}

export function SuggestionsPanel({
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
        suggestions.map((suggestion, index) => {
          const details = suggestion.details || {};
          const topic = suggestion.topic || suggestion.text || "Follow up on the current conversation";
          const phrasing = details.possibleConversation || suggestion.text || "";
          const highPriority = String(details.priority || "").toLowerCase() === "high";
          const confidence =
            typeof suggestion.confidence === "number" && Number.isFinite(suggestion.confidence)
              ? `${Math.round(suggestion.confidence * 100)}% confidence`
              : "";

          return (
            <article
              // Content-based key replays the entrance animation whenever the guidance changes.
              key={`${index}-${topic}-${phrasing.slice(0, 40)}`}
              className={`hero-card${highPriority ? " hero-card--priority" : ""}`}
            >
              <div className="hero-card__tags">
                {suggestion.type ? <span className="tag tag--accent">{suggestion.type}</span> : null}
                {details.priority ? <span className="tag tag--muted">Priority: {String(details.priority)}</span> : null}
                {confidence ? <span className="tag tag--muted">{confidence}</span> : null}
              </div>
              <h3 className="hero-card__topic">{topic}</h3>
              {phrasing ? <p className="hero-card__quote">“{phrasing}”</p> : null}
              <RecordChips ids={suggestion.linked_records || []} cases={cases} label="Based on" />
            </article>
          );
        })
      )}
    </section>
  );
}
