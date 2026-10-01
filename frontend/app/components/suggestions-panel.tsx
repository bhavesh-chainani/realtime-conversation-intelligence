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
  isListening: boolean;
  isFetchingSuggestions: boolean;
};

function formatSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

function latencyLabel(meta: SuggestionMeta): string {
  if (meta.origin === "instant") return `Instant · prepared${meta.latencyMs != null ? ` · ${formatSeconds(meta.latencyMs)}` : ""}`;
  if (meta.origin === "fallback") return "Fallback guidance";
  const total = meta.latencyMs != null ? formatSeconds(meta.latencyMs) : "";
  const llm = typeof meta.llmMs === "number" ? ` (LLM ${formatSeconds(meta.llmMs)})` : "";
  return `Live · ${total}${llm}`;
}

export function SuggestionsPanel({
  suggestions,
  meta,
  cases,
  hasTranscript,
  isListening,
  isFetchingSuggestions,
}: SuggestionsPanelProps) {
  let emptyMessage = "Start a session to receive live guidance as the conversation develops.";

  if (hasTranscript && isFetchingSuggestions) {
    emptyMessage = "Analyzing the latest conversation and preparing the next suggestion for staff.";
  } else if (hasTranscript) {
    emptyMessage = isListening
      ? "Listening for the next moment where staff guidance would be helpful."
      : "Suggestions will reappear when a new conversation is available to analyze.";
  }

  const caseById = new Map(cases.map((c) => [c.case_id, c]));

  return (
    <section className="rail-section" aria-label="AI suggestions">
      <div className="panel-heading">
        <h2 className="panel-title">AI suggestions</h2>
        {meta ? (
          <span
            className={`latency-chip latency-chip--${meta.origin}${isFetchingSuggestions ? " latency-chip--busy" : ""}`}
            title={
              meta.origin === "instant"
                ? "Prepared for this script line; replaced automatically when the live answer arrives."
                : meta.model
                  ? `Time from end of customer turn to suggestion · ${meta.model}`
                  : "Time from end of customer turn to suggestion"
            }
          >
            {latencyLabel(meta)}
          </span>
        ) : isFetchingSuggestions ? (
          <span className="latency-chip latency-chip--busy">Thinking…</span>
        ) : null}
      </div>

      {suggestions.length === 0 ? (
        <div className="empty-state">{emptyMessage}</div>
      ) : (
        <div className="suggestion-stack">
          {suggestions.map((suggestion, index) => {
            const details = suggestion.details || {};
            const topic = suggestion.topic || suggestion.text || "Follow up on the current conversation";
            const phrasing =
              details.operatorResponse ||
              details.suggestedConversation ||
              details.possibleConversation ||
              suggestion.text ||
              "";
            const priority = typeof details.priority === "string" ? details.priority : "";
            const confidence =
              typeof suggestion.confidence === "number" && Number.isFinite(suggestion.confidence)
                ? `${Math.round(suggestion.confidence * 100)}% confidence`
                : "";
            const linked = (suggestion.linked_records || []).filter((id) => caseById.has(id) || cases.length === 0);

            return (
              <article
                key={`${meta?.origin || "s"}-${suggestion.type || "suggestion"}-${index}`}
                className={`suggestion-card${linked.length ? " suggestion-card--linked" : ""}`}
              >
                <div className="suggestion-card__header">
                  <span className="tag tag--accent">{suggestion.type || `Suggestion ${index + 1}`}</span>
                  {priority ? <span className="tag tag--muted">Priority: {priority}</span> : null}
                </div>

                <div className="suggestion-card__block">
                  <div className="suggestion-card__label">Recommended focus</div>
                  <h3 className="suggestion-card__title">{topic}</h3>
                </div>

                {phrasing ? (
                  <div className="suggestion-card__block">
                    <div className="suggestion-card__label">Suggested phrasing</div>
                    <p className="suggestion-card__quote">{phrasing}</p>
                  </div>
                ) : null}

                {linked.length ? (
                  <div className="record-links" aria-label="Linked customer records">
                    <span className="record-links__label">From records</span>
                    {linked.map((id) => {
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
                ) : null}

                {confidence ? <div className="suggestion-card__footer">{confidence}</div> : null}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
