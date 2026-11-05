// Prior cases: open status and follow-up dates (YYYY-MM-DD strings, as the backend sends them).

const CLOSED_CASE_STATUSES = new Set(["resolved", "closed", "approved", "withdrawn", "completed"]);

/** Mirrors backend customer_history.is_open_case_status. */
export function isOpenCaseStatus(status: string): boolean {
  return !CLOSED_CASE_STATUSES.has(status.trim().toLowerCase());
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Mon 12 Oct" for a YYYY-MM-DD date (as given; no time zone shift). */
export function formatDueDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return iso;
  const d = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

/** Today as YYYY-MM-DD in the centre's time zone (matches the backend's CENTRE_TIMEZONE default). */
export function todayIso(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Singapore" });
}

/** True when a YYYY-MM-DD date is before `today` (YYYY-MM-DD). */
export function isOverdue(iso: string, today: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(iso) && iso < today;
}
