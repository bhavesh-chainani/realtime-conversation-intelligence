"""
Router Agent (Agent 1): Decides when to pass conversation to the suggestion agent.
This agent analyzes the conversation in real-time and determines if legal advice/suggestions are needed.
"""

import asyncio
import json
import logging
from typing import Any, Dict

from .llm import get_llm_client, get_router_model, strip_code_fences
from .prompt_loader import get_router_system_prompt, get_router_user_prompt

logger = logging.getLogger(__name__)


class RouterAgent:
    """Agent that routes conversations to the suggestion agent when legal advice is needed."""

    def __init__(self):
        self.temperature = 0.2  # Lower temperature for routing decisions

    async def should_get_suggestions(
        self,
        conversation_transcript: str,
    ) -> Dict[str, Any]:
        """
        Analyze conversation and decide if suggestions should be generated.
        Returns decision with confidence and reasoning.
        """
        if not conversation_transcript or len(conversation_transcript.strip()) < 20:
            return {
                "should_suggest": False,
                "confidence": 0.0,
                "reason": "Insufficient conversation content",
            }

        # Load prompts from external files for easy customization
        system_prompt = get_router_system_prompt()
        user_prompt = get_router_user_prompt(conversation_transcript)

        try:
            client = get_llm_client()
            if not client:
                raise ValueError("LLM API key not configured")

            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=get_router_model(),
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            decision = json.loads(strip_code_fences(response.choices[0].message.content or ""))

            # Validate structure
            if not isinstance(decision, dict):
                raise ValueError("Router agent did not return a dict")

            return {
                "should_suggest": bool(decision.get("should_suggest", False)),
                "confidence": float(decision.get("confidence", 0.5)),
                "reason": str(decision.get("reason", "No reason provided")),
                "known_info": decision.get(
                    "known_info", []
                ),  # Information already gathered
                "missing_info": decision.get(
                    "missing_info", []
                ),  # Information still needed
            }

        except Exception as e:
            logger.error(f"Router agent error: {e}")
            # Default to suggesting if there's substantial content (fail open for low latency)
            transcript_length = len(conversation_transcript.strip())
            should_suggest = transcript_length >= 50
            return {
                "should_suggest": should_suggest,
                "confidence": 0.6 if should_suggest else 0.0,
                "reason": f"Fallback decision based on transcript length: {transcript_length}",
                "known_info": [],  # Fallback: empty context
                "missing_info": [],  # Fallback: empty context
            }
