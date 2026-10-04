import type { RefObject } from "react";

import type { PendingTurn } from "../lib/stt-relay.ts";
import type { SpeakerRole, Turn } from "../lib/types.ts";

type TranscriptPanelProps = {
  turns: Turn[];
  live: string;
  /** Finished turns whose speakers are still being identified (Nemotron relay). */
  pending: PendingTurn[];
  /** Speech detected but little or no text yet (u3 models stream few partials). */
  speaking: boolean;
  status: string;
  nextVoiceIsStaff: boolean;
  hasRoleMapping: boolean;
  mappedStaffLabel?: string;
  mappedCustomerLabel?: string;
  onSetNextVoiceRole: (staff: boolean) => void;
  onSwapSpeakerRoles: () => void;
  onFlipTurn: (turnId: string) => void;
  transcriptListRef: RefObject<HTMLDivElement>;
};

function roleDisplayName(role: SpeakerRole): string {
  if (role === "staff") return "Staff";
  if (role === "customer") return "Customer";
  return "Unknown";
}

/** Short explanation of how the speaker was decided (technical view). */
function attribution(turn: Turn): string {
  switch (turn.roleSource) {
    case "diarization":
      return turn.speakerLabel ? `diarised ${turn.speakerLabel}` : "diarised";
    case "manual":
      return "set by staff";
    default:
      return "";
  }
}

export function TranscriptPanel({
  turns,
  live,
  pending,
  speaking,
  status,
  nextVoiceIsStaff,
  hasRoleMapping,
  mappedStaffLabel,
  mappedCustomerLabel,
  onSetNextVoiceRole,
  onSwapSpeakerRoles,
  onFlipTurn,
  transcriptListRef,
}: TranscriptPanelProps) {
  const mappedSpeakerCount = [mappedStaffLabel, mappedCustomerLabel].filter(Boolean).length;

  return (
    <section className="panel transcript-panel" aria-label="Live transcript workspace">
      <div className="panel-heading panel-heading--wide">
        <h2 className="panel-title">Conversation</h2>

        <div className="metric-strip" aria-label="Session metrics">
          <span className="metric-chip">{status}</span>
          <span className="metric-chip">{turns.length} turns</span>
          <span className="metric-chip">{mappedSpeakerCount}/2 voices mapped</span>
        </div>
      </div>

      <div className="control-bar control-bar--compact" role="group" aria-label="Speaker role controls">
        <div className="control-bar__content">
          <p className="control-bar__hint">Speakers from voice diarization. Click a turn to correct it.</p>

          <div className="role-toggle-row">
            <div className="role-toggle-group">
              <button
                type="button"
                className={`btn btn--toggle btn--sm${nextVoiceIsStaff ? " btn--toggle-active" : ""}`}
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
                className={`btn btn--toggle btn--sm${!nextVoiceIsStaff ? " btn--toggle-active" : ""}`}
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
              className="btn btn--ghost btn--sm"
              onClick={onSwapSpeakerRoles}
              disabled={!hasRoleMapping && turns.length === 0}
              title="Swap Staff and Customer labels if diarization inverted them"
            >
              Swap roles
            </button>
          </div>
        </div>
      </div>

      <div className="transcript-canvas">
        <div className="transcript-list" ref={transcriptListRef}>
          {turns.length === 0 && !live && !speaking && pending.length === 0 ? (
            <div className="empty-state empty-state--large">
              The conversation will appear here once the call starts.
            </div>
          ) : null}

          {turns.map((turn) => (
            <article
              key={turn.id}
              className={`turn-card turn-card--${turn.role} turn-card--clickable`}
              onClick={() => onFlipTurn(turn.id)}
              title="Click to switch Staff / Customer for this turn"
            >
              <div className="turn-card__meta">
                <span className={`role-badge role-badge--${turn.role}`}>{roleDisplayName(turn.role)}</span>
                {turn.speakerLabel ? <span className="turn-card__speaker">Speaker {turn.speakerLabel}</span> : null}
                <span className={`turn-card__source turn-card__source--${turn.roleSource}`}>{attribution(turn)}</span>
              </div>
              <p className="turn-card__text">{turn.text}</p>
            </article>
          ))}

          {pending.map((turn, i) => (
            <article key={`pending-${turn.turnOrder ?? i}`} className="turn-card turn-card--live turn-card--unknown">
              <div className="turn-card__meta">
                <span className="role-badge role-badge--unknown">…</span>
                <span className="turn-card__speaker">identifying speaker</span>
              </div>
              <p className="turn-card__text turn-card__text--live">{turn.text}</p>
            </article>
          ))}

          {live || speaking ? (
            <article className="turn-card turn-card--live turn-card--unknown" aria-live="polite">
              <div className="turn-card__meta">
                <span className="role-badge role-badge--unknown">…</span>
                <span className="turn-card__speaker">speaking</span>
              </div>
              <p className="turn-card__text turn-card__text--live">
                {live ? <span>{live} </span> : null}
                <span className="typing-dots" aria-label="speaking">
                  <span />
                  <span />
                  <span />
                </span>
              </p>
            </article>
          ) : null}
        </div>
      </div>
    </section>
  );
}
