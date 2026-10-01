import type { ReactNode } from "react";

import {
  isOpenCaseStatus,
  type CustomerData,
  type CustomerDataField,
  type CustomerHistoryCase,
  type CustomerHistoryStatus,
  type FieldSource,
  type HistoryMeta,
} from "../lib/types.ts";

type CustomerPanelProps = {
  customerData: CustomerData;
  fieldSources: Partial<Record<CustomerDataField, FieldSource>>;
  /** Per-field source tags and lookup status badge. */
  techView: boolean;
  /** Case IDs cited by the current suggestion. */
  citedIds: Set<string>;
  onCustomerDataChange: (field: CustomerDataField, value: string) => void;
  onLookup: () => void;
  customerHistoryStatus: CustomerHistoryStatus;
  customerHistoryMessage: string;
  customerHistoryCases: CustomerHistoryCase[];
  historyMeta: HistoryMeta | null;
  isLoadingCustomerHistory: boolean;
};

const SOURCE_LABELS: Record<FieldSource, string> = {
  heard: "Heard on call",
  ai: "AI extracted",
  records: "Verified from records",
  manual: "Edited by staff",
};

const HISTORY_BADGES: Record<CustomerHistoryStatus, string> = {
  idle: "Ready",
  loading: "Searching",
  invalid_input: "Need details",
  not_configured: "Unavailable",
  not_found: "No match",
  ok: "Found",
  error: "Error",
};

const HISTORY_TONES: Record<CustomerHistoryStatus, string> = {
  idle: "neutral",
  loading: "neutral",
  invalid_input: "neutral",
  not_configured: "warning",
  not_found: "neutral",
  ok: "success",
  error: "error",
};

function FieldLabel({
  htmlFor,
  source,
  showSource,
  children,
}: {
  htmlFor: string;
  source?: FieldSource;
  showSource: boolean;
  children: ReactNode;
}) {
  return (
    <label className="field-label field-label--with-source" htmlFor={htmlFor}>
      <span>{children}</span>
      {source && showSource ? <span className={`source-tag source-tag--${source}`}>{SOURCE_LABELS[source]}</span> : null}
      {source === "records" && !showSource ? (
        <span className="verified-tick" title="Verified from case records">
          ✓
        </span>
      ) : null}
    </label>
  );
}

export function CustomerPanel({
  customerData,
  fieldSources,
  techView,
  citedIds,
  onCustomerDataChange,
  onLookup,
  customerHistoryStatus,
  customerHistoryMessage,
  customerHistoryCases,
  historyMeta,
  isLoadingCustomerHistory,
}: CustomerPanelProps) {
  const canLookup =
    customerData.name.trim().length > 0 || customerData.nric_worker_permit_id.trim().length > 0;

  const historyBadge = HISTORY_BADGES[customerHistoryStatus];
  const historyTone = HISTORY_TONES[customerHistoryStatus];
  const historyMessage =
    customerHistoryStatus === "idle"
      ? "Looked up automatically once the caller's NRIC or full name is heard."
      : customerHistoryStatus === "loading"
        ? "Searching prior cases…"
        : customerHistoryMessage || "No customer history information is available yet.";
  const showCases = customerHistoryStatus === "ok" && customerHistoryCases.length > 0;
  const nameOnlyMatch = historyMeta?.matchedOn === "name";
  const verified = Object.values(fieldSources).includes("records");

  let standing: { tone: string; text: string } | null = null;
  if (historyMeta && showCases) {
    const count = customerHistoryCases.length;
    const cases = `${count} prior case${count === 1 ? "" : "s"}${historyMeta.openCount ? ` · ${historyMeta.openCount} open` : ""}`;
    standing = nameOnlyMatch
      ? { tone: "warning", text: `Possible match · verify NRIC · ${cases}` }
      : { tone: "success", text: `Returning customer · ${cases}` };
  } else if (customerHistoryStatus === "not_found") {
    standing = { tone: "neutral", text: "New customer · no prior cases" };
  }

  return (
    <section className="rail-section caller-card" aria-label="Caller details and history">
      <div className="caller-card__header">
        <h2 className="panel-title">Caller</h2>
        {standing ? <span className={`standing standing--${standing.tone}`}>{standing.text}</span> : null}
      </div>
      {verified && !techView ? (
        <div className="caller-card__verified">✓ Verified from case records (NRIC match)</div>
      ) : null}

      <div className="field-grid">
        <div className="field-group">
          <FieldLabel htmlFor="cust-name" source={fieldSources.name} showSource={techView}>
            Name
          </FieldLabel>
          <input
            id="cust-name"
            className="field-input field-input--quiet"
            type="text"
            value={customerData.name}
            onChange={(event) => onCustomerDataChange("name", event.target.value)}
            placeholder="—"
            autoComplete="off"
          />
        </div>

        <div className="field-group">
          <FieldLabel htmlFor="cust-id" source={fieldSources.nric_worker_permit_id} showSource={techView}>
            NRIC / FIN
          </FieldLabel>
          <input
            id="cust-id"
            className="field-input field-input--quiet"
            type="text"
            value={customerData.nric_worker_permit_id}
            onChange={(event) => onCustomerDataChange("nric_worker_permit_id", event.target.value)}
            placeholder="—"
            autoComplete="off"
          />
        </div>
      </div>

      <div className="field-group">
        <FieldLabel htmlFor="cust-address" source={fieldSources.address} showSource={techView}>
          Address
        </FieldLabel>
        <input
          id="cust-address"
          className="field-input field-input--quiet"
          type="text"
          value={customerData.address}
          onChange={(event) => onCustomerDataChange("address", event.target.value)}
          placeholder="—"
          autoComplete="off"
        />
      </div>

      <div className="field-group">
        <FieldLabel htmlFor="cust-purpose" source={fieldSources.purpose_of_call} showSource={techView}>
          Purpose of call
        </FieldLabel>
        <textarea
          id="cust-purpose"
          className="field-textarea field-input--quiet"
          value={customerData.purpose_of_call}
          onChange={(event) => onCustomerDataChange("purpose_of_call", event.target.value)}
          placeholder="—"
          rows={2}
        />
      </div>

      <section className="history-block" aria-live="polite">
        <div className="history-card__header">
          <div className="history-card__title">Prior cases</div>
          <div className="history-card__actions">
            {techView ? (
              <span className={`status-badge status-badge--${historyTone}`}>{historyBadge}</span>
            ) : null}
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={onLookup}
              disabled={isLoadingCustomerHistory || !canLookup}
              title={!canLookup ? "Enter a customer name or NRIC / FIN first." : "Search prior cases again"}
            >
              {isLoadingCustomerHistory ? "Looking up…" : "Look up"}
            </button>
          </div>
        </div>

        {showCases ? (
          <ul className="case-list" aria-label="Prior cases">
            {customerHistoryCases.map((row, index) => {
              const open = isOpenCaseStatus(row.status);
              const cited = citedIds.has(row.case_id);
              return (
                <li
                  key={`${row.case_id || "case"}-${index}`}
                  className={`case-item${open ? " case-item--open" : ""}${cited ? " case-item--cited" : ""}`}
                >
                  <div className="case-item__head">
                    <span className="case-item__id">
                      {row.case_id || "—"}
                      {cited ? <span className="case-item__cited">Cited</span> : null}
                    </span>
                    <span className={`case-status${open ? " case-status--open" : ""}`}>{row.status || "—"}</span>
                  </div>
                  <div className="case-item__meta">{[row.type, row.company].filter(Boolean).join(" · ")}</div>
                  {row.summary ? <p className="case-item__summary">{row.summary}</p> : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="history-card__message">{historyMessage}</p>
        )}
      </section>
    </section>
  );
}
