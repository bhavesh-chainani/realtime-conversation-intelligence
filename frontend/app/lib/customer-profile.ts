// The caller card's values and where each came from. Mirrors backend/profile.py Profile.apply;
// tests/fixtures/profile_precedence.json is checked by both test suites.
import type { CustomerData, CustomerDataField, FieldSource } from "./types.ts";

export type FieldSources = Partial<Record<CustomerDataField, FieldSource>>;
export type ProfileState = { customer: CustomerData; sources: FieldSources };

export const EMPTY_CUSTOMER: CustomerData = {
  name: "",
  nric_worker_permit_id: "",
  address: "",
  purpose_of_call: "",
};

const FIELDS = Object.keys(EMPTY_CUSTOMER) as CustomerDataField[];
export const IDENTITY_FIELDS: CustomerDataField[] = ["name", "nric_worker_permit_id"];

/**
 * Merge values from `source`. Staff edits always win; DB records beat anything heard or extracted;
 * the instant regex hears the NRIC exactly, so LLM reformatting may not replace it.
 * Returns the same object when nothing changed.
 */
export function applyCustomerPatch(
  state: ProfileState,
  patch: Partial<Record<string, string | null>>,
  source: FieldSource
): ProfileState {
  let next: ProfileState | null = null;
  for (const [key, raw] of Object.entries(patch)) {
    const field = key as CustomerDataField;
    const value = (raw || "").trim();
    if (!FIELDS.includes(field) || !value) continue;
    const current = (next ?? state).sources[field];
    if (current === "manual" || (current === "records" && source !== "records")) continue;
    if (source === "ai" && field === "nric_worker_permit_id" && current === "heard") continue;
    if ((next ?? state).customer[field] === value && current === source) continue;
    next ??= { customer: { ...state.customer }, sources: { ...state.sources } };
    next.customer[field] = value;
    next.sources[field] = source;
  }
  return next ?? state;
}

/** A staff edit: the value is kept as typed and is never overwritten by the agents. */
export function applyManualEdit(state: ProfileState, field: CustomerDataField, value: string): ProfileState {
  return {
    customer: { ...state.customer, [field]: value },
    sources: { ...state.sources, [field]: "manual" },
  };
}
