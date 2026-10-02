"""Output guard: catch model text that drifts into another script (e.g. a stray Arabic word).

Fast, low-reasoning model settings occasionally code-switch mid-sentence. Anything
shown to staff or pasted into case notes must be plain English, so callers reject
or retry outputs that contain letters outside the Latin script.
"""

from __future__ import annotations

import unicodedata
from typing import Any


class ForeignScriptError(ValueError):
    """Model output contained letters from a non-Latin script."""


def has_foreign_script(text: str) -> bool:
    """True if any letter is not Latin (punctuation, digits, symbols and accents are fine)."""
    for ch in text or "":
        if ch.isalpha() and not unicodedata.name(ch, "").startswith("LATIN"):
            return True
    return False


def contains_foreign_script(value: Any) -> bool:
    """Recursively check strings inside dicts/lists."""
    if isinstance(value, str):
        return has_foreign_script(value)
    if isinstance(value, dict):
        return any(contains_foreign_script(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_foreign_script(v) for v in value)
    return False
