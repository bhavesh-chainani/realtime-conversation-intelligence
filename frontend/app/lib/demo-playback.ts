// Pure helpers for scripted demo playback (`/?demo`): pacing, word reveal and the turns it commits.
import { DEMO_SCRIPTS, type DemoLine } from "./demo-script.ts";
import { newTurnId, type SpeakerRoleMap } from "./transcript.ts";
import type { Turn } from "./types.ts";

/** Diarisation labels the script plays as, so the badges read as they would live. */
export const DEMO_ROLE_MAP: SpeakerRoleMap = { A: "staff", B: "customer" };

export type DemoParams = { script: DemoLine[]; speed: number };

/** `?demo` or `?demo=katherine`, with an optional `&speed=1.5`; null when not in demo mode or the script is unknown. */
export function parseDemoParams(search: string): DemoParams | null {
  const params = new URLSearchParams(search);
  if (!params.has("demo")) return null;
  const script = DEMO_SCRIPTS[params.get("demo") || "katherine"];
  if (!script) return null;
  const speed = Number(params.get("speed"));
  return { script, speed: Number.isFinite(speed) && speed > 0 ? speed : 1 };
}

/** Days from the call to the follow-up Staff promises: the service guide's "call back in 7 days". */
const FOLLOW_UP_DAYS = 7;

/** The script with `{followUp}` filled in as a date, e.g. "Tuesday 13 October", so it matches the agent's date. */
export function resolveScript(script: DemoLine[], today: Date): DemoLine[] {
  const due = new Date(today.getFullYear(), today.getMonth(), today.getDate() + FOLLOW_UP_DAYS);
  const followUp = due.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  return script.map((line) => ({ ...line, text: line.text.replace(/\{followUp\}/g, followUp) }));
}

export function wordsOf(text: string): string[] {
  return text.trim().split(/\s+/).filter(Boolean);
}

/** The first `n` words of `text`, as the live line shows them. */
export function revealedText(text: string, n: number): string {
  return wordsOf(text).slice(0, Math.max(0, n)).join(" ");
}

export type LineTimings = {
  /** Typing dots before the first word. */
  leadInMs: number;
  perWordMs: number;
  /** "Identifying speaker" bubble before the turn is committed. */
  identifyMs: number;
  /** Silence after the turn, before the next speaker. */
  gapMs: number;
};

/** About 200 words a minute at speed 1. */
export function lineTimings(speed = 1): LineTimings {
  const s = speed > 0 ? speed : 1;
  return { leadInMs: 400 / s, perWordMs: 300 / s, identifyMs: 300 / s, gapMs: 700 / s };
}

/** Least time a suggestion stays on screen before Staff starts the next line, so it looks read. */
export function readingDwellMs(speed = 1): number {
  return 1500 / (speed > 0 ? speed : 1);
}

/** The committed turn for a script line. `now` is performance.now(): latency is then the real /assist time. */
export function scriptTurn(line: DemoLine, now: number): Turn {
  return {
    id: newTurnId(),
    text: line.text,
    speakerLabel: line.role === "staff" ? "A" : "B",
    role: line.role,
    roleSource: "diarization",
    committedAt: now,
    endedAt: now,
    turnEndMs: 0,
  };
}
