// Text normalisation shared by script alignment, quick entity extraction and turn dedupe.
// Keep spokenTokens/collapseSpelledRuns in sync with backend/quick_entities.py.

export const DIGIT_WORDS: Record<string, string> = {
  zero: "0",
  one: "1",
  two: "2",
  three: "3",
  four: "4",
  five: "5",
  six: "6",
  seven: "7",
  eight: "8",
  nine: "9",
};

const NUMBER_WORDS: Record<string, string> = {
  ...DIGIT_WORDS,
  ten: "10",
  eleven: "11",
  twelve: "12",
  thirteen: "13",
  fourteen: "14",
  fifteen: "15",
  sixteen: "16",
  seventeen: "17",
  eighteen: "18",
  nineteen: "19",
  twenty: "20",
  thirty: "30",
  forty: "40",
  fifty: "50",
  sixty: "60",
  seventy: "70",
  eighty: "80",
  ninety: "90",
};

const REPEAT_WORDS: Record<string, number> = { double: 2, triple: 3 };

const STOPWORDS = new Set([
  "um",
  "uh",
  "erm",
  "hmm",
  "the",
  "a",
  "an",
  "and",
  "so",
  "okay",
  "ok",
  "oh",
  "well",
  "like",
  "just",
]);

const isDigits = (t: string) => /^\d+$/.test(t);

/** Lowercase word tokens with spoken numbers mapped to digits ("double eight" -> "8 8"). */
export function spokenTokens(text: string, numberWords: Record<string, string> = DIGIT_WORDS): string[] {
  const cleaned = text
    .toLowerCase()
    .replace(/['’]/g, "")
    .replace(/[^a-z0-9]+/g, " ");
  const raw = cleaned.split(" ").filter(Boolean);
  const out: string[] = [];
  for (let i = 0; i < raw.length; i += 1) {
    const tok = raw[i];
    const repeat = REPEAT_WORDS[tok];
    if (repeat && i + 1 < raw.length) {
      const next = numberWords[raw[i + 1]] ?? raw[i + 1];
      if (/^\d$/.test(next)) {
        for (let r = 0; r < repeat; r += 1) out.push(next);
        i += 1;
        continue;
      }
    }
    if (tok === "oh" && out.length > 0 && isDigits(out[out.length - 1])) {
      out.push("0");
    } else {
      out.push(numberWords[tok] ?? tok);
    }
  }
  return out;
}

/** Join runs of single letters / digit groups containing a digit ("s 88 23451 d" -> "s8823451d"). */
export function collapseSpelledRuns(tokens: string[]): string[] {
  const out: string[] = [];
  let run: string[] = [];
  const flush = () => {
    if (run.length >= 2 && run.some(isDigits)) out.push(run.join(""));
    else out.push(...run);
    run = [];
  };
  for (const tok of tokens) {
    if (isDigits(tok) || /^[a-z]$/.test(tok) || /^[a-z]?\d+[a-z]?$/.test(tok)) {
      run.push(tok);
    } else {
      flush();
      out.push(tok);
    }
  }
  flush();
  return out;
}

/** Comparison tokens for fuzzy matching: numbers normalised, IDs collapsed, filler words dropped. */
export function tokenize(text: string): string[] {
  return collapseSpelledRuns(spokenTokens(text, NUMBER_WORDS)).filter((t) => !STOPWORDS.has(t));
}

/** True when two transcript strings are near-duplicates (>= 80% shared tokens). */
export function areSimilar(a: string, b: string): boolean {
  if (!a || !b) return false;
  const wa = tokenize(a);
  const wb = tokenize(b);
  if (wa.join(" ") === wb.join(" ")) return true;
  if (Math.abs(wa.length - wb.length) > 2) return false;
  const sa = new Set(wa);
  const sb = new Set(wb);
  const minSize = Math.min(sa.size, sb.size);
  if (minSize === 0) return false;
  let matches = 0;
  for (const w of sa) if (sb.has(w)) matches += 1;
  return matches / minSize >= 0.8;
}
