// Transcript turns and how diarisation labels (A / B) map to Staff / Customer.
import type { SpeakerRole, Turn } from "./types.ts";

export type KnownRole = "staff" | "customer";
export type SpeakerRoleMap = Record<string, KnownRole>;

/** Turns sent to /assist; the backend caps the call at 200. */
const MAX_ASSIST_TURNS = 200;

export function newTurnId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `turn-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

/**
 * Role for a diarisation label. An unseen label takes the "next voice" role if it is free, otherwise
 * the other one; a third voice stays unknown.
 */
export function resolveSpeakerRole(
  label: string | null,
  map: SpeakerRoleMap,
  nextVoiceIsStaff: boolean
): { role: SpeakerRole; map: SpeakerRoleMap } {
  if (!label) return { role: "unknown", map };
  const existing = map[label];
  if (existing) return { role: existing, map };

  const assigned = new Set(Object.values(map));
  const firstRole: KnownRole = nextVoiceIsStaff ? "staff" : "customer";
  const secondRole: KnownRole = firstRole === "staff" ? "customer" : "staff";
  for (const role of [firstRole, secondRole]) {
    if (!assigned.has(role)) return { role, map: { ...map, [label]: role } };
  }
  return { role: "unknown", map };
}

export function swapRoles(map: SpeakerRoleMap): SpeakerRoleMap {
  return Object.fromEntries(
    Object.entries(map).map(([label, role]) => [label, role === "staff" ? "customer" : "staff"])
  );
}

export function toAssistTurns(turns: Turn[]): Array<{ role: SpeakerRole; text: string }> {
  return turns.slice(-MAX_ASSIST_TURNS).map((t) => ({ role: t.role, text: t.text }));
}
