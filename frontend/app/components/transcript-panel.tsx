import type { RefObject } from "react";

type SpeakerRole = "staff" | "customer" | "unknown";

type Turn = {
  id: string;
  text: string;
  speakerLabel: string | null;
  role: SpeakerRole;
};

type TranscriptPanelProps = {
  turns: Turn[];
  live: string;
  liveRole: SpeakerRole;
  isListening: boolean;
  nextVoiceIsStaff: boolean;
  hasRoleMapping: boolean;
  mappedStaffLabel?: string;
  mappedCustomerLabel?: string;
  onSetNextVoiceRole: (staff: boolean) => void;
  onSwapSpeakerRoles: () => void;
  transcriptListRef: RefObject<HTMLDivElement>;
};

function roleDisplayName(role: SpeakerRole): string {
  if (role === "staff") return "Staff";
  if (role === "customer") return "Customer";
  return "Unknown";
}

export function TranscriptPanel({
  turns,
  live,
  liveRole,
  isListening,
  nextVoiceIsStaff,
  hasRoleMapping,
  mappedStaffLabel,
  mappedCustomerLabel,
  onSetNextVoiceRole,
  onSwapSpeakerRoles,
  transcriptListRef,
}: TranscriptPanelProps) {
  const mappedSpeakerCount = [mappedStaffLabel, mappedCustomerLabel].filter(Boolean).length;

  return (
    <section className="panel transcript-panel" aria-label="Live transcript workspace">
      <div className="panel-heading panel-heading--wide">
        <div>
          <div className="panel-kicker">Live workspace</div>
          <h2 className="panel-title">Live conversation</h2>
          <p className="panel-subtitle">
            Speaker-labeled transcript from the laptop microphone for staff and customer turns.
          </p>
        </div>

        <div className="metric-strip" aria-label="Session metrics">
          <span className="metric-chip">{isListening ? "Listening live" : "Ready"}</span>
          <span className="metric-chip">{turns.length} finalized turns</span>
          <span className="metric-chip">{mappedSpeakerCount}/2 speakers mapped</span>
        </div>
      </div>

      <div className="control-bar" role="group" aria-label="Speaker role controls">
        <div className="control-bar__content">
          <div>
            <div className="control-bar__label">Speaker assignment</div>
            <p className="control-bar__hint">
              Choose how the next unseen voice should be labeled, then swap roles only if diarization starts inverted.
            </p>
          </div>

          <div className="role-toggle-row">
            <div className="role-toggle-group">
              <button
                type="button"
                className={`btn btn--toggle${nextVoiceIsStaff ? " btn--toggle-active" : ""}`}
                onClick={() => onSetNextVoiceRole(true)}
                aria-pressed={nextVoiceIsStaff}
                disabled={hasRoleMapping && Boolean(mappedStaffLabel)}
                title={
                  mappedStaffLabel
                    ? `Staff mapped to speaker ${mappedStaffLabel}`
                    : "Lock the next unseen speaker as Staff"
                }
              >
                Next voice: Staff
              </button>
              <button
                type="button"
                className={`btn btn--toggle${!nextVoiceIsStaff ? " btn--toggle-active" : ""}`}
                onClick={() => onSetNextVoiceRole(false)}
                aria-pressed={!nextVoiceIsStaff}
                disabled={hasRoleMapping && Boolean(mappedCustomerLabel)}
                title={
                  mappedCustomerLabel
                    ? `Customer mapped to speaker ${mappedCustomerLabel}`
                    : "Lock the next unseen speaker as Customer"
                }
              >
                Next voice: Customer
              </button>
            </div>

            <button
              type="button"
              className="btn btn--ghost"
              onClick={onSwapSpeakerRoles}
              disabled={!hasRoleMapping}
              title="Swap Staff and Customer labels if diarization inverted them"
            >
              Swap roles
            </button>
          </div>
        </div>

        <div className="role-map" aria-live="polite">
          <span>{mappedStaffLabel ? `Staff ← ${mappedStaffLabel}` : "Staff ← —"}</span>
          <span>{mappedCustomerLabel ? `Customer ← ${mappedCustomerLabel}` : "Customer ← —"}</span>
        </div>
      </div>

      <div className="transcript-canvas">
        <div className="transcript-list" ref={transcriptListRef}>
          {turns.length === 0 && !live ? (
            <div className="empty-state empty-state--large">
              Start a session to capture the live conversation. Staff and customer turns will appear here as the call progresses.
            </div>
          ) : null}

          {turns.map((turn) => (
            <article key={turn.id} className={`turn-card turn-card--${turn.role}`}>
              <div className="turn-card__meta">
                <span className={`role-badge role-badge--${turn.role}`}>
                  {roleDisplayName(turn.role)}
                </span>
                {turn.speakerLabel ? (
                  <span className="turn-card__speaker">Speaker {turn.speakerLabel}</span>
                ) : null}
              </div>
              <p className="turn-card__text">{turn.text}</p>
            </article>
          ))}

          {live ? (
            <article className={`turn-card turn-card--live turn-card--${liveRole}`}>
              <div className="turn-card__meta">
                <span className={`role-badge role-badge--${liveRole}`}>
                  {roleDisplayName(liveRole)}
                </span>
                <span className="turn-card__speaker">Live partial</span>
              </div>
              <p className="turn-card__text">{live}</p>
            </article>
          ) : null}
        </div>
      </div>
    </section>
  );
}
