"""
Suggestion Agent (Agent 2): Provides real-time suggestions for operators.
This agent generates actionable suggestions when called by the router agent.
Optimized for low latency and real-time interaction.
"""

from openai import OpenAI
from typing import List, Dict, Any, Optional
import json
import logging
from .config import (
    OPENAI_API_KEY,
    SUGGESTION_MODEL,
    SUGGESTION_TEMPERATURE,
    SUGGESTION_MAX,
)
from .prompt_loader import (
    get_suggestion_system_prompt,
    get_suggestion_user_prompt,
    get_fallback_suggestions,
)

logger = logging.getLogger(__name__)


class SuggestionAgent:
    """Agent that generates real-time suggestions for operators."""

    def __init__(self):
        self.model = SUGGESTION_MODEL
        self.temperature = SUGGESTION_TEMPERATURE
        self.max_suggestions = SUGGESTION_MAX
        self.api_key = OPENAI_API_KEY
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None

    async def generate_suggestions(
        self,
        conversation_transcript: str,
        max_suggestions: Optional[int] = None,
        known_info: Optional[List[str]] = None,
        missing_info: Optional[List[str]] = None,
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
        )

        try:
            if not self.client:
                raise ValueError("OpenAI API key not configured")

            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            raw = (response.choices[0].message.content or "").strip()

            # Clean up JSON response
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.startswith("json"):
                    raw = raw[4:].lstrip()
                if raw.startswith("\n"):
                    raw = raw[1:]

            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError("Model did not return a JSON array")

            # Validate and enhance suggestions
            validated_suggestions = []
            for idx, suggestion in enumerate(parsed[:max_suggestions]):
                if not isinstance(suggestion, dict):
                    logger.warning(
                        f"Skipping invalid suggestion at index {idx}: not a dict"
                    )
                    continue

                # Ensure required fields exist
                validated = {
                    "type": suggestion.get("type", "General Suggestion"),
                    "topic": suggestion.get(
                        "topic",
                        suggestion.get(
                            "text",
                            "Follow up with the caller to gather more information.",
                        ),
                    ),
                    "confidence": float(suggestion.get("confidence", 0.7)),
                    "details": suggestion.get("details", {}),
                }

                # Ensure details structure is complete
                if not isinstance(validated["details"], dict):
                    validated["details"] = {}

                # Support new format: topic and possibleConversation
                validated["details"].setdefault(
                    "possibleConversation",
                    "Could you provide more details about your situation?",
                )
                validated["details"].setdefault(
                    "priority", suggestion.get("priority", "medium")
                )

                # Backward compatibility: map old fields to new format if needed
                if "possibleConversation" not in validated["details"]:
                    # Try to get from old field names
                    if "operatorResponse" in validated["details"]:
                        validated["details"]["possibleConversation"] = validated[
                            "details"
                        ]["operatorResponse"]
                    elif "naturalResponse" in validated["details"]:
                        validated["details"]["possibleConversation"] = validated[
                            "details"
                        ]["naturalResponse"]
                    elif "suggestedConversation" in validated["details"]:
                        # Extract operator part from conversation
                        conv = validated["details"]["suggestedConversation"]
                        if "Operator:" in conv:
                            validated["details"]["possibleConversation"] = (
                                conv.split("Operator:")[1].split("\n")[0].strip()
                            )
                        else:
                            validated["details"]["possibleConversation"] = conv

                validated_suggestions.append(validated)

            return validated_suggestions

        except Exception as e:
            logger.error(f"Suggestion agent error: {e}")
            # Return fallback suggestions from external file
            fallback = get_fallback_suggestions()
            return fallback[:1]  # Return first fallback suggestion
