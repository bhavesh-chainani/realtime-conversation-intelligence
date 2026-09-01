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
    [key: string]: unknown;
  };
};

type SuggestionsPanelProps = {
  suggestions: Suggestion[];
  hasTranscript: boolean;
  isListening: boolean;
  isFetchingSuggestions: boolean;
};

export function SuggestionsPanel({
  suggestions,
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

  return (
    <section className="rail-section" aria-label="AI suggestions">
      <div className="panel-heading">
        <div>
          <div className="panel-kicker">Operator assistant</div>
          <h2 className="panel-title">AI suggestions</h2>
          <p className="panel-subtitle">Focused guidance on what staff may want to ask or say next.</p>
        </div>
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

            return (
              <article key={`${suggestion.type || "suggestion"}-${index}`} className="suggestion-card">
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

                {confidence ? <div className="suggestion-card__footer">{confidence}</div> : null}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
