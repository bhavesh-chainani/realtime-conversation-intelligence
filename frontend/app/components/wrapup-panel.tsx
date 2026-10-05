import { useState } from "react";

import type { WrapUpStatus } from "../hooks/useWrapUp.ts";
import { formatDueDate } from "../lib/cases.ts";
import type { SaveResult, WrapUp } from "../lib/wrapup.ts";

type WrapUpPanelProps = {
  status: WrapUpStatus;
  wrapup: WrapUp | null;
  message: string;
  saving: boolean;
  saved: SaveResult | null;
  onEdit: (update: (w: WrapUp) => WrapUp) => void;
  onRetry: () => void;
  onSave: () => void;
};

const CHANNELS = ["phone", "email", "sms"];

/** After the call: the drafted case note, actions, follow-up and message, for staff to review and save. */
export function WrapUpPanel({ status, wrapup, message, saving, saved, onEdit, onRetry, onSave }: WrapUpPanelProps) {
  const [copied, setCopied] = useState(false);
  const isSaved = saved?.status === "saved";

  const copyMessage = async () => {
    if (!wrapup) return;
    const { subject, body } = wrapup.message_to_caller;
    try {
      await navigator.clipboard.writeText(subject ? `Subject: ${subject}\n\n${body}` : body);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (err) {
      console.error("[wrapup] copy failed:", err);
    }
  };

  let badge: { tone: string; text: string } = { tone: "neutral", text: "Drafting" };
  if (status === "error") badge = { tone: "error", text: "Not drafted" };
  else if (isSaved) badge = { tone: "success", text: `Saved · ${saved.caseId}` };
  else if (status === "ready") badge = { tone: "warning", text: "Review and save" };

  return (
    <section className="rail-section wrapup" aria-label="Call wrap-up">
      <div className="panel-heading">
        <h2 className="panel-title">Call wrap-up</h2>
        <span className={`status-badge status-badge--${badge.tone}`}>{badge.text}</span>
      </div>

      {status === "drafting" ? (
        <div className="hero-empty hero-empty--active">
          <span className="hero-empty__dot" aria-hidden />
          Drafting the case note, follow-up and message to the caller…
        </div>
      ) : null}

      {status === "error" ? (
        <div className="wrapup__error">
          <p>{message}</p>
          <button type="button" className="btn btn--secondary btn--sm" onClick={onRetry}>
            Try again
          </button>
        </div>
      ) : null}

      {status === "ready" && wrapup ? (
        <div className="wrapup__body">
          <div className="hero-card__tags">
            <span className="tag tag--accent">
              {wrapup.case.action === "update" ? `Updates ${wrapup.case.case_id}` : "New case"}
            </span>
            <span className="tag tag--muted">{wrapup.case.status}</span>
            {wrapup.issue_type ? <span className="tag tag--muted">{wrapup.issue_type}</span> : null}
            {wrapup.case.company ? <span className="tag tag--muted">{wrapup.case.company}</span> : null}
          </div>

          <div className="field-group">
            <label className="field-label" htmlFor="wrapup-summary">
              Case note
            </label>
            <textarea
              id="wrapup-summary"
              className="field-textarea"
              rows={4}
              value={wrapup.summary}
              onChange={(e) => onEdit((w) => ({ ...w, summary: e.target.value }))}
            />
          </div>

          {wrapup.actions.length > 0 ? (
            <div className="field-group">
              <span className="field-label">Agreed actions</span>
              <ul className="wrapup__actions">
                {wrapup.actions.map((a, i) => (
                  <li key={`${i}-${a.action}`}>
                    <span className={`wrapup__owner wrapup__owner--${a.owner}`}>
                      {a.owner === "caller" ? "Caller" : "Centre"}
                    </span>
                    <span className="wrapup__action">{a.action}</span>
                    {a.due ? <span className="wrapup__due">by {formatDueDate(a.due)}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="field-group">
            <span className="field-label">Follow-up</span>
            <div className="wrapup__follow-up">
              <input
                type="date"
                className="field-input"
                aria-label="Follow-up date"
                value={wrapup.follow_up.date}
                onChange={(e) => onEdit((w) => ({ ...w, follow_up: { ...w.follow_up, date: e.target.value } }))}
              />
              <select
                className="field-input"
                aria-label="Follow-up channel"
                value={wrapup.follow_up.channel}
                onChange={(e) => onEdit((w) => ({ ...w, follow_up: { ...w.follow_up, channel: e.target.value } }))}
              >
                {CHANNELS.map((c) => (
                  <option key={c} value={c}>
                    {c === "sms" ? "SMS" : c[0].toUpperCase() + c.slice(1)}
                  </option>
                ))}
              </select>
            </div>
            {wrapup.follow_up.reason ? <p className="wrapup__hint">{wrapup.follow_up.reason}</p> : null}
          </div>

          <div className="field-group">
            <div className="wrapup__message-head">
              <label className="field-label" htmlFor="wrapup-message">
                Message to caller ({wrapup.message_to_caller.channel === "sms" ? "SMS" : "email"})
              </label>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => void copyMessage()}>
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            {wrapup.message_to_caller.channel === "email" ? (
              <input
                className="field-input"
                aria-label="Email subject"
                value={wrapup.message_to_caller.subject}
                onChange={(e) =>
                  onEdit((w) => ({ ...w, message_to_caller: { ...w.message_to_caller, subject: e.target.value } }))
                }
              />
            ) : null}
            <textarea
              id="wrapup-message"
              className="field-textarea"
              rows={9}
              value={wrapup.message_to_caller.body}
              onChange={(e) =>
                onEdit((w) => ({ ...w, message_to_caller: { ...w.message_to_caller, body: e.target.value } }))
              }
            />
          </div>

          <div className="wrapup__footer">
            {saved && !isSaved ? <p className="wrapup__save-note">{saved.message}</p> : null}
            {isSaved ? (
              <p className="wrapup__save-note wrapup__save-note--ok">
                {saved.action === "updated" ? "Updated" : "Created"} {saved.caseId}. The next call from this caller will
                pick it up.
              </p>
            ) : null}
            <button type="button" className="btn btn--primary" onClick={onSave} disabled={saving || isSaved}>
              {saving ? "Saving…" : isSaved ? "Saved" : "Save to case system"}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
