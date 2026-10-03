"""
Suggestion Agent (Agent 2): Provides real-time suggestions for operators.
This agent generates actionable suggestions when called by the router agent.
Optimized for low latency and real-time interaction.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from .config import SUGGESTION_MAX, SUGGESTION_TEMPERATURE
from .llm import get_llm_client, get_suggestion_model, strip_code_fences
from .prompt_loader import (
    get_fallback_suggestions,
    get_suggestion_system_prompt,
    get_suggestion_user_prompt,
)

logger = logging.getLogger(__name__)


def validate_suggestion(suggestion: Any) -> Optional[Dict[str, Any]]:
    """Normalise one model-produced suggestion into the shape the UI expects."""
    if not isinstance(suggestion, dict):
        return None

    try:
        confidence = float(suggestion.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.7

    validated: Dict[str, Any] = {
        "type": suggestion.get("type", "General Suggestion"),
        "topic": suggestion.get(
            "topic",
            suggestion.get(
                "text", "Follow up with the caller to gather more information."
            ),
        ),
        "confidence": confidence,
        "details": suggestion.get("details", {}),
    }
    if not isinstance(validated["details"], dict):
        validated["details"] = {}
    details = validated["details"]

    details.setdefault(
        "possibleConversation", "Could you provide more details about your situation?"
    )
    details.setdefault("priority", suggestion.get("priority", "medium"))

    linked = suggestion.get("linked_records")
    if isinstance(linked, list):
        validated["linked_records"] = [str(x).strip() for x in linked if str(x).strip()]
    if isinstance(suggestion.get("source"), str):
        validated["source"] = suggestion["source"].strip().lower()

    return validated


class SuggestionAgent:
    """Agent that generates real-time suggestions for operators."""

    def __init__(self):
        self.temperature = SUGGESTION_TEMPERATURE
        self.max_suggestions = SUGGESTION_MAX

    async def generate_suggestions(
        self,
        conversation_transcript: str,
        max_suggestions: Optional[int] = None,
        known_info: Optional[List[str]] = None,
        missing_info: Optional[List[str]] = None,
        customer_record: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Generate real-time suggestions for the operator.
        Optimized for low latency - returns quickly with actionable suggestions.

        Args:
            conversation_transcript: The full conversation transcript
            max_suggestions: Maximum number of suggestions to generate
            known_info: List of information that has already been gathered (from router agent)
            missing_info: List of information gaps that still need to be addressed (from router agent)
        """
        if not conversation_transcript or len(conversation_transcript.strip()) < 10:
            return []

        max_suggestions = max(1, min(5, max_suggestions or self.max_suggestions))

        # Load prompts from external files for easy customization
        system_prompt = get_suggestion_system_prompt()
        user_prompt = get_suggestion_user_prompt(
            conversation_transcript,
            max_suggestions,
            known_info=known_info or [],
            missing_info=missing_info or [],
            customer_record=customer_record,
        )

        try:
            client = get_llm_client()
            if not client:
                raise ValueError("LLM API key not configured")

            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=get_suggestion_model(),
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            parsed = json.loads(strip_code_fences(response.choices[0].message.content or ""))
            if not isinstance(parsed, list):
                raise ValueError("Model did not return a JSON array")

            validated_suggestions = []
            for idx, suggestion in enumerate(parsed[:max_suggestions]):
                validated = validate_suggestion(suggestion)
                if validated is None:
                    logger.warning(
                        f"Skipping invalid suggestion at index {idx}: not a dict"
                    )
                    continue
                validated_suggestions.append(validated)

            return validated_suggestions

        except Exception as e:
            logger.error(f"Suggestion agent error: {e}")
            # Return fallback suggestions from external file
            fallback = get_fallback_suggestions()
            return fallback[:1]  # Return first fallback suggestion
