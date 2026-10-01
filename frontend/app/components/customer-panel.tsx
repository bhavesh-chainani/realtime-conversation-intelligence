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

function FieldLabel({
  htmlFor,
  source,
  children,
}: {
  htmlFor: string;
  source?: FieldSource;
  children: ReactNode;
}) {
  return (
    <label className="field-label field-label--with-source" htmlFor={htmlFor}>
      <span>{children}</span>
      {source ? <span className={`source-tag source-tag--${source}`}>{SOURCE_LABELS[source]}</span> : null}
    </label>
  );
}

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

export function CustomerPanel({
  customerData,
  fieldSources,
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
      ? "Prior cases are looked up automatically once the caller's NRIC or full name is heard."
      : customerHistoryStatus === "loading"
        ? "Searching the backend for prior customer cases…"
        : customerHistoryMessage || "No customer history information is available yet.";
  const showHistoryTable = customerHistoryStatus === "ok" && customerHistoryCases.length > 0;
  const nameOnlyMatch = historyMeta?.matchedOn === "name";

  return (
    <section className="rail-section" aria-label="Customer profile and history">
      <div className="panel-heading">
        <h2 className="panel-title">Customer profile</h2>
      </div>

      {historyMeta && customerHistoryCases.length > 0 ? (
        <div
          className={`returning-banner${nameOnlyMatch ? " returning-banner--unverified" : ""}`}
          role="status"
        >
          <strong>
            {nameOnlyMatch ? "Possible returning customer" : "Returning customer"} ·{" "}
            {customerHistoryCases.length} prior case{customerHistoryCases.length === 1 ? "" : "s"}
            {historyMeta.openCount ? `, ${historyMeta.openCount} open` : ""}
          </strong>
          <span>
            {nameOnlyMatch
              ? "Matched by name only. Verify NRIC / FIN before discussing case details."
              : historyMeta.companies.join(", ") || "Verified on NRIC / FIN."}
          </span>
        </div>
      ) : null}

      <div className="field-grid">
        <div className="field-group">
          <FieldLabel htmlFor="cust-name" source={fieldSources.name}>
            Name
          </FieldLabel>
          <input
            id="cust-name"
            className="field-input"
            type="text"
            value={customerData.name}
            onChange={(event) => onCustomerDataChange("name", event.target.value)}
            placeholder="Customer name"
            autoComplete="off"
          />
        </div>

        <div className="field-group">
          <FieldLabel htmlFor="cust-id" source={fieldSources.nric_worker_permit_id}>
            NRIC / Work Permit ID
          </FieldLabel>
          <input
            id="cust-id"
            className="field-input"
            type="text"
            value={customerData.nric_worker_permit_id}
            onChange={(event) => onCustomerDataChange("nric_worker_permit_id", event.target.value)}
            placeholder="e.g. S1234567A"
            autoComplete="off"
          />
        </div>
      </div>

      <div className="field-group">
        <FieldLabel htmlFor="cust-address" source={fieldSources.address}>
          Address
        </FieldLabel>
        <textarea
          id="cust-address"
          className="field-textarea"
          value={customerData.address}
          onChange={(event) => onCustomerDataChange("address", event.target.value)}
          placeholder="Customer address"
          rows={3}
        />
      </div>

      <div className="field-group">
        <FieldLabel htmlFor="cust-purpose" source={fieldSources.purpose_of_call}>
          Purpose of call
        </FieldLabel>
        <textarea
          id="cust-purpose"
          className="field-textarea"
          value={customerData.purpose_of_call}
          onChange={(event) => onCustomerDataChange("purpose_of_call", event.target.value)}
          placeholder="Reason for the call"
          rows={3}
        />
      </div>

      <section className={`history-card history-card--${historyTone}`} aria-live="polite">
        <div className="history-card__header">
          <div className="history-card__title">Case history</div>
          <div className="history-card__actions">
            <span className={`status-badge status-badge--${historyTone}`}>{historyBadge}</span>
            <button
              type="button"
              className="btn btn--secondary btn--sm"
              onClick={onLookup}
              disabled={isLoadingCustomerHistory || !canLookup}
              title={!canLookup ? "Enter a customer name or NRIC / Work Permit ID first." : "Search prior cases again"}
            >
              {isLoadingCustomerHistory ? "Looking up…" : "Look up"}
            </button>
          </div>
        </div>

        {showHistoryTable ? (
          <ul className="case-list" aria-label="Prior cases">
            {customerHistoryCases.map((row, index) => {
              const open = isOpenCaseStatus(row.status);
              return (
                <li key={`${row.case_id || "case"}-${index}`} className={`case-item${open ? " case-item--open" : ""}`}>
                  <div className="case-item__head">
                    <span className="case-item__id">{row.case_id || "—"}</span>
                    <span className={`case-status${open ? " case-status--open" : ""}`}>{row.status || "—"}</span>
                  </div>
                  <div className="case-item__meta">
                    {[row.type, row.company].filter(Boolean).join(" · ")}
                  </div>
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
