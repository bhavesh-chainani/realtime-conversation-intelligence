// Instant (no-LLM) identity capture from customer speech. Mirrors backend/quick_entities.py.
import { collapseSpelledRuns, spokenTokens } from "./text-normalize.ts";

// Checksum is intentionally not enforced: demo personas use dummy IDs.
const NRIC_IN_TOKEN = /[stfgm]\d{7}[a-z]/;
const NRIC_EXACT = /^[STFGM]\d{7}[A-Z]$/;
const INTRO_NAME =
  /\b(?:[Mm]y name is|[Mm]y name's|[Tt]his is|I am|I'm)\s+((?:[A-Z][a-zA-Z'-]+)(?:\s+(?:[A-Z][a-zA-Z'-]+)){1,3})/;
const NAME_STOPWORDS = new Set(["Calling", "From", "Here", "The", "And", "Not", "Just", "Really"]);

export function isNricShape(value: string): boolean {
  return NRIC_EXACT.test(value.replace(/\s+/g, "").toUpperCase());
}

/** First NRIC/FIN-shaped ID in written or spoken text, uppercased. */
export function extractNric(text: string): string | null {
  if (!text) return null;
  for (const tok of collapseSpelledRuns(spokenTokens(text))) {
    const match = NRIC_IN_TOKEN.exec(tok);
    if (match) return match[0].toUpperCase();
  }
  return null;
}

/** Self-introduced, capitalised full name ("my name is Sarah Lim"), else null. */
export function extractIntroName(text: string): string | null {
  if (!text) return null;
  const match = INTRO_NAME.exec(text);
  if (!match) return null;
  const words = match[1].split(/\s+/).filter((w) => !NAME_STOPWORDS.has(w));
  return words.length >= 2 ? words.join(" ") : null;
}
