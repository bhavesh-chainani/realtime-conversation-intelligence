import { Fragment, type RefObject } from "react";

import type { PendingTurn } from "../lib/stt-relay.ts";
import type { Moment, SpeakerRole, Turn } from "../lib/types.ts";

type TranscriptPanelProps = {
  turns: Turn[];
  live: string;
  /** Finished turns whose speakers are still being identified (Nemotron relay). */
  pending: PendingTurn[];
  /** Speech detected but little or no text yet (u3 models stream few partials). */
  speaking: boolean;
  liveRole: SpeakerRole;
  moments: Moment[];
  /** Show attribution captions, speaker labels and speaker controls. */
  techView: boolean;
  status: string;
  scriptGuided: boolean;
  nextVoiceIsStaff: boolean;
  hasRoleMapping: boolean;
  mappedStaffLabel?: string;
  mappedCustomerLabel?: string;
  onSetNextVoiceRole: (staff: boolean) => void;
  onSwapSpeakerRoles: () => void;
  onFlipTurn: (turnId: string) => void;
  transcriptListRef: RefObject<HTMLDivElement>;
};

const MOMENT_ICONS: Record<Moment["kind"], string> = {
  success: "✓",
  warning: "!",
  info: "i",
  link: "↗",
  wrapup: "✎",
};

function roleDisplayName(role: SpeakerRole): string {
  if (role === "staff") return "Staff";
  if (role === "customer") return "Customer";
  return "Unknown";
}

/** Short explanation of how the speaker was decided (technical view). */
function attribution(turn: Turn): string {
  switch (turn.roleSource) {
    case "script":
      return `script ${turn.scriptLineId ?? ""}${typeof turn.alignScore === "number" ? ` · ${Math.min(1, turn.alignScore).toFixed(2)}` : ""}`;
    case "diarization":
      return turn.speakerLabel ? `diarised ${turn.speakerLabel}` : "diarised";
    case "alternation":
      return "turn order";
    case "manual":
      return "set by staff";
    default:
      return "";
  }
}

function MomentChip({ moment }: { moment: Moment }) {
  return (
    <div className={`moment moment--${moment.kind}`} role="status">
      <span className="moment__icon" aria-hidden>
        {MOMENT_ICONS[moment.kind]}
      </span>
      {moment.text}
    </div>
  );
}

export function TranscriptPanel({
  turns,
  live,
  pending,
  speaking,
  liveRole,
  moments,
  techView,
  status,
  scriptGuided,
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
  const momentsByTurn = new Map<string | null, Moment[]>();
  for (const m of moments) {
    const list = momentsByTurn.get(m.afterTurnId) ?? [];
    list.push(m);
    momentsByTurn.set(m.afterTurnId, list);
  }

  return (
    <section className="panel transcript-panel" aria-label="Live transcript workspace">
      <div className="panel-heading panel-heading--wide">
        <h2 className="panel-title">Conversation</h2>

        {techView ? (
          <div className="metric-strip" aria-label="Session metrics">
            <span className="metric-chip">{status}</span>
            <span className="metric-chip">{turns.length} turns</span>
            {!scriptGuided ? (
              <span className="metric-chip">{mappedSpeakerCount}/2 voices mapped</span>
            ) : null}
          </div>
        ) : null}
      </div>

      {techView ? (
        <div className="control-bar control-bar--compact" role="group" aria-label="Speaker role controls">
          <div className="control-bar__content">
            <p className="control-bar__hint">
              {scriptGuided ? "Speakers matched to the script." : "Speakers from voice diarization."} Click a turn
              to correct it.
            </p>

            <div className="role-toggle-row">
              {!scriptGuided ? (
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
              ) : null}

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
      ) : null}

      <div className="transcript-canvas">
        <div className="transcript-list" ref={transcriptListRef}>
          {turns.length === 0 && !live && !speaking && pending.length === 0 ? (
            <div className="empty-state empty-state--large">The conversation will appear here once the call starts.</div>
          ) : null}

          {(momentsByTurn.get(null) ?? []).map((m) => (
            <MomentChip key={m.id} moment={m} />
          ))}

          {turns.map((turn) => (
            <Fragment key={turn.id}>
              <article
                className={`turn-card turn-card--${turn.role} turn-card--clickable`}
                onClick={() => onFlipTurn(turn.id)}
                title="Click to switch Staff / Customer for this turn"
              >
                <div className="turn-card__meta">
                  <span className={`role-badge role-badge--${turn.role}`}>{roleDisplayName(turn.role)}</span>
                  {techView && turn.speakerLabel ? (
                    <span className="turn-card__speaker">Speaker {turn.speakerLabel}</span>
                  ) : null}
                  {techView ? (
                    <span className={`turn-card__source turn-card__source--${turn.roleSource}`}>
                      {attribution(turn)}
                    </span>
                  ) : null}
                </div>
                <p className="turn-card__text">{turn.text}</p>
              </article>
              {(momentsByTurn.get(turn.id) ?? []).map((m) => (
                <MomentChip key={m.id} moment={m} />
              ))}
            </Fragment>
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
            <article className={`turn-card turn-card--live turn-card--${liveRole}`} aria-live="polite">
              <div className="turn-card__meta">
                <span className={`role-badge role-badge--${liveRole}`}>{roleDisplayName(liveRole)}</span>
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
