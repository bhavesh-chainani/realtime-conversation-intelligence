"""
Prompt loader utility for loading prompts from external files.
This allows users to easily edit prompts without modifying Python code.
"""

import pathlib
import json
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Base directory for prompts
PROMPTS_DIR = pathlib.Path(__file__).parent / "prompts"


def load_prompt(filename: str) -> str:
    """
    Load a prompt from a text file in the prompts directory.

    Args:
        filename: Name of the prompt file (e.g., 'router_system_prompt.txt')

    Returns:
        Content of the prompt file as a string

    Raises:
        FileNotFoundError: If the prompt file doesn't exist
    """
    prompt_path = PROMPTS_DIR / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        logger.debug(f"Loaded prompt from {filename}")
        return content
    except Exception as e:
        logger.error(f"Error loading prompt from {filename}: {e}")
        raise


def load_json_prompt(filename: str) -> Any:
    """
    Load a JSON prompt/file from the prompts directory.

    Args:
        filename: Name of the JSON file (e.g., 'fallback_suggestions.json')

    Returns:
        Parsed JSON content

    Raises:
        FileNotFoundError: If the JSON file doesn't exist
        json.JSONDecodeError: If the JSON is invalid
    """
    prompt_path = PROMPTS_DIR / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"JSON prompt file not found: {prompt_path}")

    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = json.load(f)
        logger.debug(f"Loaded JSON prompt from {filename}")
        return content
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {filename}: {e}")
        raise
    except Exception as e:
        logger.error(f"Error loading JSON prompt from {filename}: {e}")
        raise


def format_prompt(template: str, **kwargs) -> str:
    """
    Format a prompt template with provided variables.

    Args:
        template: Prompt template string with {variable_name} placeholders
        **kwargs: Variables to substitute into the template

    Returns:
        Formatted prompt string
    """
    try:
        return template.format(**kwargs)
    except KeyError as e:
        logger.error(f"Missing template variable: {e}")
        raise
    except Exception as e:
        logger.error(f"Error formatting prompt: {e}")
        raise


# Cached prompt loaders for performance
_cached_prompts: Dict[str, str] = {}


def get_router_system_prompt() -> str:
    """Get the router agent system prompt (cached)."""
    if "router_system" not in _cached_prompts:
        _cached_prompts["router_system"] = load_prompt("router_system_prompt.txt")
    return _cached_prompts["router_system"]


def get_router_user_prompt(conversation_transcript: str) -> str:
    """Get the router agent user prompt formatted with conversation transcript."""
    if "router_user_template" not in _cached_prompts:
        _cached_prompts["router_user_template"] = load_prompt("router_user_prompt.txt")
    template = _cached_prompts["router_user_template"]
    return format_prompt(template, conversation_transcript=conversation_transcript)


def get_suggestion_system_prompt() -> str:
    """Get the suggestion agent system prompt (cached)."""
    if "suggestion_system" not in _cached_prompts:
        _cached_prompts["suggestion_system"] = load_prompt(
            "suggestion_system_prompt.txt"
        )
    return _cached_prompts["suggestion_system"]


def get_suggestion_user_prompt(
    conversation_transcript: str,
    max_suggestions: int,
    known_info: Optional[List[str]] = None,
    missing_info: Optional[List[str]] = None,
    customer_record: Optional[str] = None,
) -> str:
    """Get the suggestion agent user prompt formatted with conversation transcript, max suggestions, and context."""
    if "suggestion_user_template" not in _cached_prompts:
        _cached_prompts["suggestion_user_template"] = load_prompt(
            "suggestion_user_prompt.txt"
        )
    template = _cached_prompts["suggestion_user_template"]

    # Format known_info and missing_info as readable strings
    known_info_str = (
        "\n".join(f"- {info}" for info in (known_info or []))
        if known_info
        else "None identified yet"
    )
    missing_info_str = (
        "\n".join(f"- {info}" for info in (missing_info or []))
        if missing_info
        else "All essential information gathered"
    )

    return format_prompt(
        template,
        conversation_transcript=conversation_transcript,
        max_suggestions=max_suggestions,
        known_info=known_info_str,
        missing_info=missing_info_str,
        customer_record=customer_record or NO_CUSTOMER_RECORD,
    )


NO_CUSTOMER_RECORD = (
    "CUSTOMER RECORD: not yet retrieved. Do not mention or guess at prior cases."
)

# Statuses that mean a case needs no further action; anything else counts as open.
CLOSED_CASE_STATUSES = {"resolved", "closed", "approved", "withdrawn", "completed"}


def is_open_case_status(status: Optional[str]) -> bool:
    return (status or "").strip().lower() not in CLOSED_CASE_STATUSES


def format_customer_record(
    profile: Optional[Dict[str, Any]], cases: Optional[List[Dict[str, Any]]]
) -> str:
    """Render verified customer data + prior cases as a compact prompt block."""
    profile = profile or {}
    cases = [c for c in (cases or []) if isinstance(c, dict) and c.get("case_id")]
    if not cases:
        return NO_CUSTOMER_RECORD

    ident = " | ".join(
        f"{label}: {profile[key]}"
        for key, label in (("name", "Name"), ("nric_worker_permit_id", "NRIC"))
        if profile.get(key)
    )
    open_count = sum(1 for c in cases if is_open_case_status(c.get("status")))
    if profile.get("record_match") == "name":
        header = (
            "CUSTOMER RECORD (possible match by NAME ONLY - identity NOT verified yet; "
            "ask for NRIC / FIN before discussing any case details):"
        )
    else:
        header = "CUSTOMER RECORD (verified from the case system, matched on NRIC):"
    lines = [header]
    if ident:
        lines.append(ident)
    lines.append(f"Prior cases ({len(cases)}; {open_count} open):")
    for c in cases:
        status = str(c.get("status") or "").strip()
        status_label = f"{status.upper()} (open)" if is_open_case_status(status) else status
        lines.append(
            "- "
            + " | ".join(
                part
                for part in (
                    str(c.get("case_id") or "").strip(),
                    str(c.get("company") or "").strip(),
                    str(c.get("type") or "").strip(),
                    status_label,
                    str(c.get("summary") or "").strip(),
                )
                if part
            )
        )
    return "\n".join(lines)


def get_suggestion_fast_system_prompt() -> str:
    """System prompt for the single-call (router + suggester merged) pipeline."""
    if "suggestion_fast_system" not in _cached_prompts:
        _cached_prompts["suggestion_fast_system"] = load_prompt(
            "suggestion_fast_system_prompt.txt"
        )
    return _cached_prompts["suggestion_fast_system"]


def get_suggestion_fast_user_prompt(
    conversation_transcript: str, max_suggestions: int, customer_record: Optional[str]
) -> str:
    if "suggestion_fast_user_template" not in _cached_prompts:
        _cached_prompts["suggestion_fast_user_template"] = load_prompt(
            "suggestion_fast_user_prompt.txt"
        )
    return format_prompt(
        _cached_prompts["suggestion_fast_user_template"],
        conversation_transcript=conversation_transcript,
        max_suggestions=max_suggestions,
        customer_record=customer_record or NO_CUSTOMER_RECORD,
    )


def get_fallback_suggestions() -> List[Dict[str, Any]]:
    """Get fallback suggestions from JSON file (cached)."""
    if "fallback_suggestions" not in _cached_prompts:
        _cached_prompts["fallback_suggestions"] = load_json_prompt(
            "fallback_suggestions.json"
        )
    # Return a copy to avoid mutation
    import copy

    return copy.deepcopy(_cached_prompts["fallback_suggestions"])


def reload_prompts():
    """Clear cached prompts to force reload from files. Useful for development/testing."""
    _cached_prompts.clear()
    logger.info("Prompt cache cleared. Prompts will be reloaded on next access.")
