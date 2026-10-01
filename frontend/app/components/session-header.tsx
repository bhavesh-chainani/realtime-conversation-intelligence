type SessionHeaderProps = {
  isLive: boolean;
  isAuthenticated: boolean;
  showAuthButton: boolean;
  /** Hidden in demo mode, where the demo bar owns start/stop. */
  showSessionControls: boolean;
  onLogin: () => void;
  onLogout: () => void;
  onStart: () => void;
  onStop: () => void;
};

export function SessionHeader({
  isLive,
  isAuthenticated,
  showAuthButton,
  showSessionControls,
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
      </div>

      <div className="app-header__actions">
        <span className={`status-pill${isLive ? " status-pill--live" : ""}`} aria-live="polite">
          <span className="status-pill__dot" aria-hidden />
          {isLive ? "Call in progress" : "Ready for next call"}
        </span>

        {showAuthButton ? (
          isAuthenticated ? (
            <button type="button" className="btn btn--ghost btn--sm" onClick={onLogout}>
              Logout
            </button>
          ) : (
            <button type="button" className="btn btn--ghost btn--sm" onClick={onLogin}>
              Login
            </button>
          )
        ) : null}

        {showSessionControls ? (
          isLive ? (
            <button type="button" className="btn btn--secondary btn--sm" onClick={onStop}>
              Stop
            </button>
          ) : (
            <button type="button" className="btn btn--primary btn--sm" onClick={onStart}>
              Start session
            </button>
          )
        ) : null}
      </div>
    </header>
  );
}
