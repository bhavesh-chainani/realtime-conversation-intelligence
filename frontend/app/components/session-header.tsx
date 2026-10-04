import { useEffect, useState } from "react";

type SessionHeaderProps = {
  isLive: boolean;
  /** Mic starting up: tell presenters not to speak yet. */
  isConnecting: boolean;
  callerName: string | null;
  /** Date.now() when the call started / ended (null when not applicable). */
  startedAt: number | null;
  endedAt: number | null;
  micLevel: number;
  canEndCall: boolean;
  onEndCall: () => void;
  /** Hold the call: stop listening but keep everything on screen. */
  canPause: boolean;
  canResume: boolean;
  onPause: () => void;
  onResume: () => void;
  /** The call has ended: offer to clear the workspace for the next one. */
  canStartNewCall: boolean;
  onNewCall: () => void;
  onStart: () => void;
  onStop: () => void;
};

function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

/** Four bars driven by mic level. */
function ListeningBars({ level }: { level: number }) {
  const heights = [0.55, 1, 0.75, 0.45].map((w) => Math.max(0.18, Math.min(1, level * 1.6 * w)));
  return (
    <span className="listen-bars" aria-label="Listening">
      {heights.map((h, i) => (
        <span key={i} style={{ transform: `scaleY(${h})` }} />
      ))}
    </span>
  );
}

export function SessionHeader({
  isLive,
  isConnecting,
  callerName,
  startedAt,
  endedAt,
  micLevel,
  canEndCall,
  onEndCall,
  canPause,
  canResume,
  onPause,
  onResume,
  canStartNewCall,
  onNewCall,
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
    const who = callerName ? ` · ${callerName}` : "";
    const state = isLive ? "On call" : isConnecting ? "Connecting…" : "On hold";
    status = `${state}${who} · ${formatClock(now - startedAt)}`;
    tone = isLive ? "live" : isConnecting ? "connecting" : "hold";
  } else if (isConnecting) {
    status = "Connecting…";
    tone = "connecting";
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
          {isLive ? <ListeningBars level={micLevel} /> : null}
        </span>

        {canPause ? (
          <button type="button" className="btn btn--secondary btn--sm" onClick={onPause} title="Stop listening, keep everything on screen">
            Pause
          </button>
        ) : null}
        {canResume ? (
          <button type="button" className="btn btn--primary btn--sm" onClick={onResume} title="Resume listening">
            Resume
          </button>
        ) : null}

        {canEndCall ? (
          <button type="button" className="btn btn--danger btn--sm" onClick={onEndCall}>
            End call
          </button>
        ) : null}

        {canStartNewCall ? (
          <button type="button" className="btn btn--primary btn--sm" onClick={onNewCall}>
            New call
          </button>
        ) : isLive ? (
          <button type="button" className="btn btn--secondary btn--sm" onClick={onStop}>
            Stop
          </button>
        ) : (
          <button type="button" className="btn btn--primary btn--sm" onClick={onStart}>
            Start session
          </button>
        )}
      </div>
    </header>
  );
}
