"""Loads agent prompts from backend/prompts/<agent>/ so they can be edited without touching code.

Each agent has `system.md` (sent as is) and `user.md` (a str.format template). Only user templates
are formatted: system prompts contain literal JSON braces.
"""

from __future__ import annotations

import copy
import json
import pathlib
from functools import cache
from typing import Any

PROMPTS_DIR = pathlib.Path(__file__).parent / "prompts"


@cache
def load(agent: str, name: str) -> str:
    """Text of backend/prompts/<agent>/<name>. Raises FileNotFoundError if it is missing."""
    return (PROMPTS_DIR / agent / name).read_text(encoding="utf-8").strip()


def system_prompt(agent: str) -> str:
    return load(agent, "system.md")


def user_prompt(agent: str, **variables: Any) -> str:
    return load(agent, "user.md").format(**variables)


@cache
def _fallback_suggestions() -> list[dict[str, Any]]:
    return json.loads(load("suggestion", "fallback.json"))


def fallback_suggestions() -> list[dict[str, Any]]:
    """Static suggestions shown when the agent fails (a copy, safe to modify)."""
    return copy.deepcopy(_fallback_suggestions())
