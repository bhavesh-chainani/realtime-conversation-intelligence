// Spoken-ID normalisation for quick entity extraction. Keep in sync with backend/quick_entities.py.

const DIGIT_WORDS: Record<string, string> = {
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

const REPEAT_WORDS: Record<string, number> = { double: 2, triple: 3 };

const isDigits = (t: string) => /^\d+$/.test(t);

/** Lowercase word tokens with spoken numbers mapped to digits ("double eight" -> "8 8"). */
export function spokenTokens(text: string): string[] {
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
      const next = DIGIT_WORDS[raw[i + 1]] ?? raw[i + 1];
      if (/^\d$/.test(next)) {
        for (let r = 0; r < repeat; r += 1) out.push(next);
        i += 1;
        continue;
      }
    }
    if (tok === "oh" && out.length > 0 && isDigits(out[out.length - 1])) {
      out.push("0");
    } else {
      out.push(DIGIT_WORDS[tok] ?? tok);
    }
  }
  return out;
}

/** Join runs of single letters / digit groups containing a digit ("s 12 34567 a" -> "s1234567a"). */
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
