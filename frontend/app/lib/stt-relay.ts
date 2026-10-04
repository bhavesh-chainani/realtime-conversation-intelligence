// Messages from the backend STT relay (/ws/stt): AssemblyAI words, speakers from Nemotron diarisation.

/** Where to connect, from GET /stt/session. */
export type RelayInfo = { path: string; ticket: string };

/** A finished turn whose speakers are still being identified. */
export type PendingTurn = { turnOrder: number | undefined; text: string };

/** One speaker's part of a finished turn (speaker null when the diariser did not hear it). */
export type RelaySegment = { text: string; speakerLabel: string | null };

export function normalizeSpeakerLabel(raw: unknown): string | null {
  if (raw == null) return null;
  const label = String(raw).trim().toUpperCase();
  return label && label !== "UNKNOWN" && label !== "NULL" && label !== "NONE" ? label : null;
}

export function parseRelayInfo(payload: Record<string, unknown>): RelayInfo | null {
  if (typeof payload.path !== "string" || typeof payload.ticket !== "string") return null;
  return { path: payload.path, ticket: payload.ticket };
}

export function relayUrl(backendUrl: string, relay: RelayInfo, sampleRate: number): string {
  const base = backendUrl.replace(/^http/, "ws").replace(/\/$/, "");
  const query = new URLSearchParams({ ticket: relay.ticket, sample_rate: String(sampleRate) });
  return `${base}${relay.path}?${query}`;
}

export function parsePendingTurn(msg: Record<string, unknown>): PendingTurn | null {
  if (msg.type !== "PendingTurn") return null;
  return {
    turnOrder: typeof msg.turn_order === "number" ? msg.turn_order : undefined,
    text: String(msg.transcript || "").trim(),
  };
}

/** Speaker segments of a relay final turn, or null if the message is not one. */
export function parseRelaySegments(msg: Record<string, unknown>): RelaySegment[] | null {
  if (!Array.isArray(msg.segments)) return null;
  return msg.segments
    .map((raw) => {
      const seg = raw as Record<string, unknown>;
      return { text: String(seg.transcript || "").trim(), speakerLabel: normalizeSpeakerLabel(seg.speaker_label) };
    })
    .filter((seg) => seg.text);
}
