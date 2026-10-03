// Messages from the backend STT relay (/ws/stt): AssemblyAI turns re-labelled by Nemotron diarisation.

export type RelayInfo = { path: string; ticket: string };

/** A finished turn whose speakers are still being identified. */
export type PendingTurn = { turnOrder: number | undefined; text: string; speakerLabel: string | null };

/** One speaker's part of a finished turn. */
export type RelaySegment = { text: string; speakerLabel: string | null; wordLabels?: Array<string | null> };

export function normalizeSpeakerLabel(raw: unknown): string | null {
  if (raw == null) return null;
  const label = String(raw).trim().toUpperCase();
  // AssemblyAI marks words it has not attributed yet as "PENDING": not a third voice.
  if (!label || label === "UNKNOWN" || label === "NULL" || label === "NONE" || label === "PENDING") {
    return null;
  }
  return label;
}

/** The relay to use, when the token endpoint offered one (Nemotron diarisation is on). */
export function parseRelayInfo(payload: Record<string, unknown>): RelayInfo | null {
  const relay = payload.relay as Record<string, unknown> | undefined;
  if (!relay || typeof relay.path !== "string" || typeof relay.ticket !== "string") return null;
  return { path: relay.path, ticket: relay.ticket };
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
    speakerLabel: normalizeSpeakerLabel(msg.speaker_label),
  };
}

/** Speaker segments of a relay final turn, or null if the message is not one. */
export function parseRelaySegments(msg: Record<string, unknown>): RelaySegment[] | null {
  if (!Array.isArray(msg.segments)) return null;
  return msg.segments
    .map((raw) => {
      const seg = raw as Record<string, unknown>;
      const words = Array.isArray(seg.words) ? (seg.words as Array<Record<string, unknown>>) : [];
      return {
        text: String(seg.transcript || "").trim(),
        speakerLabel: normalizeSpeakerLabel(seg.speaker_label),
        wordLabels: words.length ? words.map((w) => normalizeSpeakerLabel(w.speaker)) : undefined,
      };
    })
    .filter((seg) => seg.text);
}
