"""Fast, deterministic entity extraction from transcript text (no LLM).

Runs on every customer turn before any LLM call (backend/orchestrator.py), so a spoken phone number,
email or self-introduced name starts the customer DB lookup within milliseconds.
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

# Singapore numbers: 8 digits starting 3 (VoIP), 6 (landline), 8 or 9 (mobile).
PHONE_PATTERN = re.compile(r"[3689]\d{7}")
EMAIL_PATTERN = re.compile(r"[a-z0-9][\w.+-]*@[a-z0-9-]+(?:\.[a-z0-9-]+)+")
SPOKEN_EMAIL_WORDS = {
    "dot": ".",
    "underscore": "_",
    "dash": "-",
    "hyphen": "-",
    "at": "@",
}
SPOKEN_EMAIL_TLDS = {"com", "sg", "net", "org", "edu", "gov", "co", "io"}
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


def normalize_phone(value: str | None) -> str | None:
    """A Singapore phone number as 8 digits ("+65 9123 4567" -> "91234567"), or None if it is not one."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 10 and digits.startswith("65"):
        digits = digits[2:]
    return digits if PHONE_PATTERN.fullmatch(digits) else None


def normalize_email(value: str | None) -> str | None:
    """A lowercased email with no whitespace, or None if it is not email-shaped."""
    text = re.sub(r"\s+", "", value or "").lower()
    return text if EMAIL_PATTERN.fullmatch(text) else None


def extract_phone(text: str) -> str | None:
    """Return the first Singapore phone number in free text (spoken or written), as 8 digits."""
    run: list[str] = []
    for tok in [*_spoken_tokens(text or ""), ""]:
        if tok.isdigit():
            run.append(tok)
            continue
        found = normalize_phone("".join(run)) if run else None
        if found:
            return found
        run.clear()
    return None


def extract_email(text: str) -> str | None:
    """Return the first email in free text: written ("a.b@x.com") or spoken ("a dot b at x dot com")."""
    if not text:
        return None
    match = EMAIL_PATTERN.search(text.lower())
    if match:
        return match.group(0)
    spoken = re.sub(r"[,;]", " ", text.lower())
    for word, symbol in SPOKEN_EMAIL_WORDS.items():
        spoken = re.sub(rf"\s+{word}\s+", symbol, spoken)
    match = EMAIL_PATTERN.search(spoken)
    # Spoken forms must end in a common TLD, so "at work dot ..." style phrases are not emails.
    if match and match.group(0).rsplit(".", 1)[-1] in SPOKEN_EMAIL_TLDS:
        return match.group(0)
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


def _latest_on_customer_lines(transcript: str, extract) -> str | None:
    """Scan Customer: lines only, latest first."""
    for line in reversed(customer_lines(transcript)):
        found = extract(line)
        if found:
            return found
    return None


def extract_phone_from_transcript(transcript: str) -> str | None:
    return _latest_on_customer_lines(transcript, extract_phone)


def extract_email_from_transcript(transcript: str) -> str | None:
    return _latest_on_customer_lines(transcript, extract_email)


def quick_patch(text: str) -> dict[str, str]:
    """Identity fields heard in one line of customer speech, for the instant (pre-LLM) DB lookup."""
    patch = {
        "contact_number": extract_phone(text),
        "email": extract_email(text),
        "name": extract_intro_name(text),
    }
    return {k: v for k, v in patch.items() if v}


def quick_patch_from_lines(texts: list[str]) -> dict[str, str]:
    """Identity fields heard across several lines of customer speech; the latest mention of each wins."""
    patch: dict[str, str] = {}
    for text in reversed(texts):
        for key, value in quick_patch(text).items():
            patch.setdefault(key, value)
    return patch
