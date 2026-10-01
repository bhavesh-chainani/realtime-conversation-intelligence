// Script-guided speaker attribution.
//
// Each finalized STT turn is fuzzy-matched against a small window of upcoming script
// lines. A confident match fixes the speaker role (and advances the script cursor);
// weaker matches defer to diarization; unmatched ad-libs fall back to diarization or
// turn alternation. Confident matches also vote on which diarization label (A/B) is
// which role, so an inverted label map corrects itself after a couple of turns.
import { tokenize } from "./text-normalize.ts";

export type ScriptRole = "staff" | "customer";
export type RoleSource = "script" | "diarization" | "alternation" | "manual";

export type ScriptLine = {
  id: string;
  role: ScriptRole;
  text: string;
  say?: string;
  beat?: { label: string; expect?: { linked_records?: string[]; gist?: string } };
};

export type LabelVotes = Record<string, { staff: number; customer: number }>;

export type AlignState = {
  cursor: number;
  consumed: Record<string, number>;
  labelVotes: LabelVotes;
};

export type AlignConfidence = "high" | "low" | "none";

export type AlignSegment = {
  text: string;
  role: ScriptRole | null;
  source: RoleSource;
  lineId: string | null;
  score: number;
  confidence: AlignConfidence;
  speakerLabel: string | null;
};

export type AlignInput = {
  text: string;
  speakerLabel: string | null;
  /** Per-word diarization labels aligned to text.split(/\s+/); used to split merged turns. */
  wordLabels?: Array<string | null>;
  /** Role currently mapped to a diarization label (null if unmapped/unknown). */
  labelRole: (label: string | null) => ScriptRole | null;
  prevRole: ScriptRole | null;
};

export const ALIGN_HIGH = 0.55;
export const ALIGN_LOW = 0.35;
export const WINDOW_BEHIND = 1;
export const WINDOW_AHEAD = 3;
export const DISTANCE_PENALTY = 0.05;
export const MERGE_MARGIN = 0.12;
export const CONSUMED_TO_ADVANCE = 0.6;

export function initialAlignState(): AlignState {
  return { cursor: 0, consumed: {}, labelVotes: {} };
}

export function opposite(role: ScriptRole): ScriptRole {
  return role === "staff" ? "customer" : "staff";
}

function within1Edit(a: string, b: string): boolean {
  if (Math.abs(a.length - b.length) > 1) return false;
  let i = 0;
  let j = 0;
  let edits = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      i += 1;
      j += 1;
      continue;
    }
    edits += 1;
    if (edits > 1) return false;
    if (a.length > b.length) i += 1;
    else if (b.length > a.length) j += 1;
    else {
      i += 1;
      j += 1;
    }
  }
  return edits + (a.length - i) + (b.length - j) <= 1;
}

function tokenMatch(a: string, b: string): boolean {
  if (a === b) return true;
  const shortest = Math.min(a.length, b.length);
  if (shortest >= 5 && within1Edit(a, b)) return true;
  // Plurals / inflections: "payslip" ~ "payslips", "complain" ~ "complained".
  return shortest >= 4 && Math.abs(a.length - b.length) <= 2 && (a.startsWith(b) || b.startsWith(a));
}

function trigramDice(a: string, b: string): number {
  const grams = (s: string) => {
    const out = new Map<string, number>();
    for (let i = 0; i < s.length - 2; i += 1) {
      const g = s.slice(i, i + 3);
      out.set(g, (out.get(g) || 0) + 1);
    }
    return out;
  };
  const ga = grams(a);
  const gb = grams(b);
  let overlap = 0;
  let total = 0;
  for (const [g, n] of ga) {
    overlap += Math.min(n, gb.get(g) || 0);
    total += n;
  }
  for (const n of gb.values()) total += n;
  return total === 0 ? 0 : (2 * overlap) / total;
}

export type ScoreResult = { score: number; precision: number; recall: number };

/** Precision-weighted fuzzy token overlap between a heard turn and a script line. */
export function scoreTokens(turn: string[], line: string[]): ScoreResult {
  if (turn.length === 0 || line.length === 0) return { score: 0, precision: 0, recall: 0 };
  const used = new Array<boolean>(turn.length).fill(false);
  let matchedLine = 0;
  let matchedTurn = 0;
  for (const lt of line) {
    let hit = false;
    for (let i = 0; i < turn.length; i += 1) {
      if (!used[i] && tokenMatch(turn[i], lt)) {
        used[i] = true;
        matchedLine += 1;
        matchedTurn += 1;
        hit = true;
        break;
      }
    }
    if (hit) continue;
    // Compound split by STT: "bright path" vs "brightpath".
    for (let i = 0; i < turn.length - 1; i += 1) {
      if (!used[i] && !used[i + 1] && tokenMatch(turn[i] + turn[i + 1], lt)) {
        used[i] = true;
        used[i + 1] = true;
        matchedLine += 1;
        matchedTurn += 2;
        break;
      }
    }
  }
  const precision = matchedTurn / turn.length;
  const recall = matchedLine / line.length;
  let score = 0.6 * precision + 0.4 * recall;
  if (trigramDice(turn.join(" "), line.join(" ")) >= 0.6) score += 0.05;
  return { score, precision, recall };
}

const lineTokenCache = new WeakMap<ScriptLine, string[][]>();

function lineVariants(line: ScriptLine): string[][] {
  let cached = lineTokenCache.get(line);
  if (!cached) {
    cached = [tokenize(line.text)];
    if (line.say) cached.push(tokenize(line.say));
    lineTokenCache.set(line, cached);
  }
  return cached;
}

export function scoreLine(turn: string[], line: ScriptLine): ScoreResult {
  let best: ScoreResult = { score: 0, precision: 0, recall: 0 };
  for (const variant of lineVariants(line)) {
    const s = scoreTokens(turn, variant);
    if (s.score > best.score) best = s;
  }
  return best;
}

function cloneState(state: AlignState): AlignState {
  const labelVotes: LabelVotes = {};
  for (const [label, v] of Object.entries(state.labelVotes)) labelVotes[label] = { ...v };
  return { cursor: state.cursor, consumed: { ...state.consumed }, labelVotes };
}

function markConsumed(state: AlignState, lines: ScriptLine[], idx: number, recall: number) {
  const id = lines[idx].id;
  const consumed = Math.min(1, (state.consumed[id] || 0) + recall);
  state.consumed[id] = consumed;
  // A partly heard line (STT split it) holds the cursor so the rest can still match it.
  state.cursor = Math.max(state.cursor, consumed >= CONSUMED_TO_ADVANCE ? idx + 1 : idx);
}

function vote(state: AlignState, label: string | null, role: ScriptRole) {
  if (!label) return;
  const votes = (state.labelVotes[label] ??= { staff: 0, customer: 0 });
  votes[role] += 1;
}

function majorityLabel(labels: Array<string | null> | undefined): string | null {
  if (!labels) return null;
  const counts = new Map<string, number>();
  for (const l of labels) if (l) counts.set(l, (counts.get(l) || 0) + 1);
  let best: string | null = null;
  let bestN = 0;
  for (const [l, n] of counts) {
    if (n > bestN) {
      best = l;
      bestN = n;
    }
  }
  return best;
}

function fallbackSegment(
  text: string,
  input: AlignInput,
  lines: ScriptLine[],
  state: AlignState,
  score: number
): AlignSegment {
  const diarRole = input.labelRole(input.speakerLabel);
  if (diarRole) {
    return { text, role: diarRole, source: "diarization", lineId: null, score, confidence: "none", speakerLabel: input.speakerLabel };
  }
  const role = input.prevRole ? opposite(input.prevRole) : lines[state.cursor]?.role ?? null;
  return { text, role, source: "alternation", lineId: null, score, confidence: "none", speakerLabel: input.speakerLabel };
}

/** Align one finalized turn; returns the new state and one segment (or two if the turn merged speakers). */
export function alignTurn(
  lines: ScriptLine[],
  state: AlignState,
  input: AlignInput
): { state: AlignState; segments: AlignSegment[] } {
  const next = cloneState(state);
  const text = input.text.trim();
  const tok = tokenize(text);
  if (lines.length === 0 || tok.length === 0) {
    return { state: next, segments: [fallbackSegment(text, input, lines, next, 0)] };
  }

  const lo = Math.max(0, next.cursor - WINDOW_BEHIND);
  const hi = Math.min(lines.length - 1, next.cursor + WINDOW_AHEAD);

  let bestIdx = -1;
  let bestAdj = -Infinity;
  let bestRaw: ScoreResult = { score: 0, precision: 0, recall: 0 };
  for (let i = lo; i <= hi; i += 1) {
    const raw = scoreLine(tok, lines[i]);
    const adj = raw.score - DISTANCE_PENALTY * Math.abs(i - next.cursor);
    if (adj > bestAdj) {
      bestAdj = adj;
      bestIdx = i;
      bestRaw = raw;
    }
  }

  // Did diarization merge two adjacent lines from different speakers into one turn?
  const words = text.split(/\s+/).filter(Boolean);
  if (tok.length >= 6 && words.length >= 4) {
    let mergeIdx = -1;
    let mergeAdj = -Infinity;
    for (let k = lo; k < hi; k += 1) {
      if (lines[k].role === lines[k + 1].role) continue;
      const combined = [...lineVariants(lines[k])[0], ...lineVariants(lines[k + 1])[0]];
      const adj = scoreTokens(tok, combined).score - DISTANCE_PENALTY * Math.abs(k - next.cursor);
      if (adj > mergeAdj) {
        mergeAdj = adj;
        mergeIdx = k;
      }
    }
    if (mergeIdx >= 0 && mergeAdj >= ALIGN_HIGH && mergeAdj - bestAdj >= MERGE_MARGIN) {
      const k = mergeIdx;
      const labelsUsable = input.wordLabels?.length === words.length ? input.wordLabels : undefined;
      let splitAt = 1;
      let splitScore = -Infinity;
      for (let i = 1; i < words.length; i += 1) {
        let s =
          scoreLine(tokenize(words.slice(0, i).join(" ")), lines[k]).score +
          scoreLine(tokenize(words.slice(i).join(" ")), lines[k + 1]).score;
        if (labelsUsable && labelsUsable[i] && labelsUsable[i] !== labelsUsable[i - 1]) s += 0.15;
        if (s > splitScore) {
          splitScore = s;
          splitAt = i;
        }
      }
      const parts: Array<{ text: string; idx: number; labels?: Array<string | null> }> = [
        { text: words.slice(0, splitAt).join(" "), idx: k, labels: labelsUsable?.slice(0, splitAt) },
        { text: words.slice(splitAt).join(" "), idx: k + 1, labels: labelsUsable?.slice(splitAt) },
      ];
      const segments = parts.map((part): AlignSegment => {
        const line = lines[part.idx];
        const raw = scoreLine(tokenize(part.text), line);
        const label = majorityLabel(part.labels);
        markConsumed(next, lines, part.idx, raw.recall);
        vote(next, label, line.role);
        return { text: part.text, role: line.role, source: "script", lineId: line.id, score: raw.score, confidence: "high", speakerLabel: label };
      });
      return { state: next, segments };
    }
  }

  const line = lines[bestIdx];
  const confidence: AlignConfidence =
    bestAdj >= ALIGN_HIGH && tok.length >= 2 ? "high" : bestAdj >= ALIGN_LOW ? "low" : "none";
  const score = Math.max(0, bestAdj);

  if (confidence === "high") {
    markConsumed(next, lines, bestIdx, bestRaw.recall);
    vote(next, input.speakerLabel, line.role);
    return {
      state: next,
      segments: [{ text, role: line.role, source: "script", lineId: line.id, score, confidence, speakerLabel: input.speakerLabel }],
    };
  }

  if (confidence === "low") {
    const diarRole = input.labelRole(input.speakerLabel);
    if (diarRole === null || diarRole === line.role) {
      // Only advance the script when diarization independently agrees.
      if (diarRole === line.role) markConsumed(next, lines, bestIdx, bestRaw.recall);
      return {
        state: next,
        segments: [{ text, role: line.role, source: "script", lineId: line.id, score, confidence, speakerLabel: input.speakerLabel }],
      };
    }
    return {
      state: next,
      segments: [{ text, role: diarRole, source: "diarization", lineId: null, score, confidence, speakerLabel: input.speakerLabel }],
    };
  }

  return { state: next, segments: [fallbackSegment(text, input, lines, next, score)] };
}

/**
 * Label map implied by script votes, or null if unchanged. Unmapped labels map on one
 * vote; flipping an existing mapping needs a margin of 2. The map stays one-to-one.
 */
export function deriveLabelMap(
  votes: LabelVotes,
  current: Record<string, ScriptRole>
): Record<string, ScriptRole> | null {
  const next: Record<string, ScriptRole> = { ...current };
  let changed = false;
  for (const [label, v] of Object.entries(votes)) {
    const margin = v.staff - v.customer;
    if (margin === 0) continue;
    const winner: ScriptRole = margin > 0 ? "staff" : "customer";
    const have = next[label];
    if (have === winner) continue;
    if (Math.abs(margin) >= (have === undefined ? 1 : 2)) {
      next[label] = winner;
      for (const other of Object.keys(next)) {
        if (other !== label && next[other] === winner) next[other] = opposite(winner);
      }
      changed = true;
    }
  }
  return changed ? next : null;
}

/** Script lines before the cursor that were never heard. */
export function skippedLineIds(lines: ScriptLine[], state: AlignState): string[] {
  return lines.slice(0, state.cursor).filter((l) => !state.consumed[l.id]).map((l) => l.id);
}

/** Re-apply a corrected label map to turns that were attributed by diarization only. */
export function relabelDiarizedTurns<
  T extends { role: string; roleSource: RoleSource; speakerLabel: string | null },
>(turns: T[], map: Record<string, ScriptRole>): T[] {
  let changed = false;
  const out = turns.map((t) => {
    if (t.roleSource !== "diarization" || !t.speakerLabel) return t;
    const role = map[t.speakerLabel];
    if (!role || role === t.role) return t;
    changed = true;
    return { ...t, role };
  });
  return changed ? out : turns;
}
