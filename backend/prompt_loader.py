"""Loads prompts from backend/prompts so they can be edited without touching Python code."""

from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

PROMPTS_DIR = pathlib.Path(__file__).parent / "prompts"

_cache: dict[str, Any] = {}


def load_prompt(filename: str) -> str:
    """Text of a prompt file (cached). Raises FileNotFoundError if it is missing."""
    if filename not in _cache:
        _cache[filename] = (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()
    return _cache[filename]


def load_json_prompt(filename: str) -> Any:
    """Parsed JSON file from the prompts directory (cached; returns a copy)."""
    if filename not in _cache:
        _cache[filename] = json.loads((PROMPTS_DIR / filename).read_text(encoding="utf-8"))
    return copy.deepcopy(_cache[filename])


def get_suggestion_system_prompt() -> str:
    return load_prompt("suggestion_system_prompt.txt")


def get_suggestion_user_prompt(
    conversation_transcript: str, max_suggestions: int, customer_record: str
) -> str:
    return load_prompt("suggestion_user_prompt.txt").format(
        conversation_transcript=conversation_transcript,
        max_suggestions=max_suggestions,
        customer_record=customer_record,
    )


def get_fallback_suggestions() -> list[dict[str, Any]]:
    return load_json_prompt("fallback_suggestions.json")
