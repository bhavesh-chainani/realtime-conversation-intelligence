type CustomerHistoryStatus =
  | "idle"
  | "loading"
  | "invalid_input"
  | "not_configured"
  | "not_found"
  | "ok"
  | "error";

type CustomerData = {
  name: string;
  nric_worker_permit_id: string;
  address: string;
  purpose_of_call: string;
};

type CustomerHistoryCase = {
  case_id: string;
  company: string;
  type: string;
  status: string;
  summary: string;
};

type CustomerPanelProps = {
  customerData: CustomerData;
  onCustomerDataChange: (
    field: "name" | "nric_worker_permit_id" | "address" | "purpose_of_call",
    value: string
  ) => void;
  onLookup: () => void;
  customerHistoryStatus: CustomerHistoryStatus;
  customerHistoryMessage: string;
  customerHistoryCases: CustomerHistoryCase[];
  isLoadingCustomerHistory: boolean;
};

const HISTORY_TITLES: Record<CustomerHistoryStatus, string> = {
  idle: "Customer history",
  loading: "Customer history",
  invalid_input: "More details needed",
  not_configured: "Lookup unavailable",
  not_found: "No history found",
  ok: "Customer history",
  error: "Lookup failed",
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

export function CustomerPanel({
  customerData,
  onCustomerDataChange,
  onLookup,
  customerHistoryStatus,
  customerHistoryMessage,
  customerHistoryCases,
  isLoadingCustomerHistory,
}: CustomerPanelProps) {
  const filledFieldCount = [
    customerData.name,
    customerData.nric_worker_permit_id,
    customerData.address,
    customerData.purpose_of_call,
  ].filter((value) => value.trim().length > 0).length;

  const canLookup =
    customerData.name.trim().length > 0 || customerData.nric_worker_permit_id.trim().length > 0;

  const historyTitle = HISTORY_TITLES[customerHistoryStatus];
  const historyBadge = HISTORY_BADGES[customerHistoryStatus];
  const historyTone = HISTORY_TONES[customerHistoryStatus];
  const historyMessage =
    customerHistoryStatus === "idle"
      ? "Review the extracted details, make any corrections, then search prior customer cases."
      : customerHistoryStatus === "loading"
        ? "Searching the backend for prior customer cases…"
        : customerHistoryMessage || "No customer history information is available yet.";
  const showHistoryTable = customerHistoryStatus === "ok" && customerHistoryCases.length > 0;

  return (
    <section className="rail-section" aria-label="Customer profile and history">
      <div className="panel-heading">
        <div>
          <div className="panel-kicker">Customer workspace</div>
          <h2 className="panel-title">Customer profile</h2>
          <p className="panel-subtitle">
            Auto-filled details from the call. Staff edits are preserved and can be refined before lookup.
          </p>
        </div>
      </div>

      <div className="summary-grid" aria-label="Customer profile summary">
        <article className="summary-card">
          <span className="summary-card__label">Captured fields</span>
          <strong className="summary-card__value">{filledFieldCount}/4</strong>
          <p className="summary-card__hint">Name, NRIC, address, and purpose are kept ready for staff review.</p>
        </article>
        <article className="summary-card">
          <span className="summary-card__label">History lookup</span>
          <strong className="summary-card__value">{historyBadge}</strong>
          <p className="summary-card__hint">Searches prefer NRIC / Work Permit ID, then fall back to customer name.</p>
        </article>
      </div>

      <div className="field-grid">
        <div className="field-group">
          <label className="field-label" htmlFor="cust-name">
            Name
          </label>
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
          <label className="field-label" htmlFor="cust-id">
            NRIC / Work Permit ID
          </label>
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
        <label className="field-label" htmlFor="cust-address">
          Address
        </label>
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
        <label className="field-label" htmlFor="cust-purpose">
          Purpose of call
        </label>
        <textarea
          id="cust-purpose"
          className="field-textarea"
          value={customerData.purpose_of_call}
          onChange={(event) => onCustomerDataChange("purpose_of_call", event.target.value)}
          placeholder="Reason for the call"
          rows={3}
        />
      </div>

      <div className="lookup-bar">
        <div>
          <div className="lookup-bar__title">Customer history lookup</div>
          <p className="lookup-bar__hint">Use the reviewed identity details to retrieve prior customer cases for staff context.</p>
        </div>

        <button
          type="button"
          className="btn btn--primary"
          onClick={onLookup}
          disabled={isLoadingCustomerHistory || !canLookup}
          title={!canLookup ? "Enter a customer name or NRIC / Work Permit ID first." : undefined}
        >
          {isLoadingCustomerHistory ? "Obtaining…" : "Obtain customer info"}
        </button>
      </div>

      <section className={`history-card history-card--${historyTone}`} aria-live="polite">
        <div className="history-card__header">
          <div>
            <div className="history-card__title">{historyTitle}</div>
            <p className="history-card__message">{historyMessage}</p>
          </div>
          <span className={`status-badge status-badge--${historyTone}`}>{historyBadge}</span>
        </div>

        {showHistoryTable ? (
          <div className="history-table-wrap">
            <table className="history-table" aria-label="Customer case history table">
              <thead>
                <tr>
                  <th>Case ID</th>
                  <th>Company</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Summary</th>
                </tr>
              </thead>
              <tbody>
                {customerHistoryCases.map((row, index) => (
                  <tr key={`${row.case_id || "case"}-${index}`}>
                    <td>{row.case_id || "—"}</td>
                    <td>{row.company || "—"}</td>
                    <td>{row.type || "—"}</td>
                    <td>{row.status || "—"}</td>
                    <td>{row.summary || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </section>
  );
}
