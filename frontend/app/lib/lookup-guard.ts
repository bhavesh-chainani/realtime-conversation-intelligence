// Decides when the auto customer-history lookup should fire, so it runs once per identity.
import { isNricShape } from "./quick-entities.ts";

export type LookupRequest = {
  key: string;
  name?: string;
  nric_worker_permit_id?: string;
};

/**
 * Returns the lookup to run, or null. A valid ID always wins and supersedes an earlier
 * name lookup; a full name (2+ words) is used only until an ID lookup has happened.
 */
export function nextLookup(
  previousKey: string | null,
  profile: { name: string; nric_worker_permit_id: string }
): LookupRequest | null {
  const id = profile.nric_worker_permit_id.replace(/\s+/g, "").toUpperCase();
  if (id && isNricShape(id)) {
    const key = `id:${id}`;
    return key === previousKey ? null : { key, nric_worker_permit_id: id };
  }
  if (previousKey?.startsWith("id:")) return null;

  const name = profile.name.trim().replace(/\s+/g, " ");
  if (name.split(" ").length >= 2) {
    const key = `name:${name.toLowerCase()}`;
    return key === previousKey ? null : { key, name };
  }
  return null;
}
