import { useEffect, useState } from "react";

type SessionHeaderProps = {
  isLive: boolean;
  callerName: string | null;
  /** Date.now() when the call started / ended (null when not applicable). */
  startedAt: number | null;
  endedAt: number | null;
  audioMode: "mic" | "autopilot" | null;
  micLevel: number;
  canEndCall: boolean;
  onEndCall: () => void;
  isAuthenticated: boolean;
  showAuthButton: boolean;
  /** Hidden in demo mode, where the presenter dock owns start/stop. */
  showSessionControls: boolean;
  onLogin: () => void;
  onLogout: () => void;
  onStart: () => void;
  onStop: () => void;
};

function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

/** Four bars driven by mic level; autopilot animates them since there is no audio. */
function ListeningBars({ mode, level }: { mode: "mic" | "autopilot"; level: number }) {
  const heights = [0.55, 1, 0.75, 0.45].map((w) => Math.max(0.18, Math.min(1, level * 1.6 * w)));
  return (
    <span className={`listen-bars${mode === "autopilot" ? " listen-bars--auto" : ""}`} aria-label="Listening">
      {heights.map((h, i) => (
        <span key={i} style={mode === "mic" ? { transform: `scaleY(${h})` } : undefined} />
      ))}
    </span>
  );
}

export function SessionHeader({
  isLive,
  callerName,
  startedAt,
  endedAt,
  audioMode,
  micLevel,
  canEndCall,
  onEndCall,
  isAuthenticated,
  showAuthButton,
  showSessionControls,
  onLogin,
  onLogout,
  onStart,
  onStop,
}: SessionHeaderProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedAt === null || endedAt !== null) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [startedAt, endedAt]);

  let status = "Ready for next call";
  let tone = "idle";
  if (endedAt !== null && startedAt !== null) {
    status = `Call ended · ${formatClock(endedAt - startedAt)}`;
    tone = "ended";
  } else if (startedAt !== null) {
    status = `On call${callerName ? ` · ${callerName}` : ""} · ${formatClock(now - startedAt)}`;
    tone = isLive ? "live" : "hold";
  }

  return (
    <header className="app-header panel">
      <div className="app-header__brand">
        <div className="app-header__eyebrow">Staff workspace</div>
        <h1 className="app-header__title">Conversation intelligence</h1>
      </div>

      <div className="app-header__actions">
        <span className={`call-status call-status--${tone}`} aria-live="polite">
          <span className="call-status__dot" aria-hidden />
          <span className="call-status__text">{status}</span>
          {audioMode ? <ListeningBars mode={audioMode} level={micLevel} /> : null}
        </span>

        {canEndCall ? (
          <button type="button" className="btn btn--danger btn--sm" onClick={onEndCall}>
            End call
          </button>
        ) : null}

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
