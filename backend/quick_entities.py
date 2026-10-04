"""Fast, deterministic entity extraction from transcript text (no LLM).

Mirrors frontend/app/lib/quick-entities.ts so the browser and the backend
agree on when an NRIC / name becomes known during a call.
"""

from __future__ import annotations

import re

DIGIT_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}
REPEAT_WORDS = {"double": 2, "triple": 3}

# Checksum is intentionally not enforced: the seeded demo DB uses dummy IDs.
NRIC_PATTERN = re.compile(r"[stfgm]\d{7}[a-z]")
INTRO_NAME_PATTERN = re.compile(
    r"\b(?:[Mm]y name is|[Mm]y name's|[Tt]his is|I am|I'm)\s+"
    r"((?:[A-Z][a-zA-Z'-]+)(?:\s+(?:[A-Z][a-zA-Z'-]+)){1,3})"
)
NAME_STOPWORDS = {"Calling", "From", "Here", "The", "And", "Not", "Just", "Really"}


def _spoken_tokens(text: str) -> list[str]:
    cleaned = re.sub(r"['’]", "", text.lower())
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    out: list[str] = []
    raw = cleaned.split()
    i = 0
    while i < len(raw):
        tok = raw[i]
        if tok in REPEAT_WORDS and i + 1 < len(raw):
            nxt = DIGIT_WORDS.get(raw[i + 1], raw[i + 1])
            if nxt.isdigit() and len(nxt) == 1:
                out.extend([nxt] * REPEAT_WORDS[tok])
                i += 2
                continue
        if tok == "oh" and out and out[-1].isdigit():
            out.append("0")
        else:
            out.append(DIGIT_WORDS.get(tok, tok))
        i += 1
    return out


def collapse_spelled_runs(tokens: list[str]) -> list[str]:
    """Join runs of single letters / digit groups that contain a digit ("s 12 34567 a" -> "s1234567a")."""
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if len(run) >= 2 and any(t.isdigit() for t in run):
            out.append("".join(run))
        else:
            out.extend(run)
        run.clear()

    for tok in tokens:
        if tok.isdigit() or (len(tok) == 1 and tok.isalpha()) or re.fullmatch(r"[a-z]?\d+[a-z]?", tok):
            run.append(tok)
        else:
            flush()
            out.append(tok)
    flush()
    return out


def extract_nric(text: str) -> str | None:
    """Return the first NRIC/FIN-shaped ID in free text (spoken or written), uppercased."""
    if not text:
        return None
    for tok in collapse_spelled_runs(_spoken_tokens(text)):
        match = NRIC_PATTERN.search(tok)
        if match:
            return match.group(0).upper()
    return None


def extract_intro_name(text: str) -> str | None:
    """Return a self-introduced, capitalised name ("my name is Katherine Liao") or None."""
    if not text:
        return None
    match = INTRO_NAME_PATTERN.search(text)
    if not match:
        return None
    words = [w for w in match.group(1).split() if w not in NAME_STOPWORDS]
    return " ".join(words) if len(words) >= 2 else None


def customer_lines(transcript: str) -> list[str]:
    return [
        line.split(":", 1)[1].strip()
        for line in (transcript or "").splitlines()
        if line.lower().startswith("customer:")
    ]


def extract_nric_from_transcript(transcript: str) -> str | None:
    """Scan Customer: lines only, latest first."""
    for line in reversed(customer_lines(transcript)):
        found = extract_nric(line)
        if found:
            return found
    return None
