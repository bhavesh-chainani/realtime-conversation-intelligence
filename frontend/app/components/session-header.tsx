type SessionHeaderProps = {
  isListening: boolean;
  isAuthenticated: boolean;
  showAuthButton: boolean;
  onLogin: () => void;
  onLogout: () => void;
  onStart: () => void;
  onStop: () => void;
};

export function SessionHeader({
  isListening,
  isAuthenticated,
  showAuthButton,
  onLogin,
  onLogout,
  onStart,
  onStop,
}: SessionHeaderProps) {
  return (
    <header className="app-header panel">
      <div className="app-header__brand">
        <div className="app-header__eyebrow">Staff workspace</div>
        <h1 className="app-header__title">Conversation intelligence</h1>
        <p className="app-header__subtitle">
          Live transcription, operator guidance, and customer context in one calm workspace.
        </p>
        <div className="app-header__meta" aria-label="Workspace capabilities">
          <span className="meta-chip">Realtime transcription</span>
          <span className="meta-chip">AI suggestions</span>
          <span className="meta-chip">Customer history</span>
        </div>
      </div>

      <div className="app-header__actions">
        <div className="session-indicator" aria-live="polite">
          <span className={`status-pill${isListening ? " status-pill--live" : ""}`}>
            <span className="status-pill__dot" aria-hidden />
            {isListening ? "Live" : "Idle"}
          </span>
          <div className="session-indicator__copy">
            <strong>{isListening ? "Session in progress" : "Ready for the next call"}</strong>
            <span>
              {isListening
                ? "The microphone stream is active and the assistant is following the conversation."
                : "Start a session when staff are ready to capture a live conversation."}
            </span>
          </div>
        </div>

        <div className="app-header__button-row">
          {showAuthButton ? (
            isAuthenticated ? (
              <button type="button" className="btn btn--ghost" onClick={onLogout}>
                Logout
              </button>
            ) : (
              <button type="button" className="btn btn--ghost" onClick={onLogin}>
                Login
              </button>
            )
          ) : null}

          <button type="button" className="btn btn--primary" onClick={onStart} disabled={isListening}>
            Start session
          </button>
          <button type="button" className="btn btn--secondary" onClick={onStop} disabled={!isListening}>
            Stop
          </button>
        </div>
      </div>
    </header>
  );
}
