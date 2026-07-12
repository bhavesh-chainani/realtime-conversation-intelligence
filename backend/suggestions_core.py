"""Core suggestion inference (sync API + worker)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

from .router_agent import RouterAgent
from .suggestion_agent import SuggestionAgent
from .prompt_loader import get_fallback_suggestions

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

_router_agent = RouterAgent()
_suggestion_agent = SuggestionAgent()


async def compute_suggestions(
    context: str, max_suggestions: int = 2
) -> Dict[str, Any]:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("=" * 80)
    logger.info(f"[SUGGESTION REQUEST] {timestamp}")
    logger.info("-" * 80)
    logger.info(f"Input Conversation Transcript ({len(context)} chars):")
    logger.info(f"'{context}'")
    logger.info("-" * 80)

    try:
        logger.info(
            "[Agent 1: Router] Analyzing conversation to decide if suggestions are needed..."
        )
        router_decision = await _router_agent.should_get_suggestions(context)

        logger.info(
            f"[Agent 1: Router] Decision: should_suggest={router_decision['should_suggest']}, confidence={router_decision['confidence']:.2f}, reason={router_decision['reason']}"
        )

        known_info = router_decision.get("known_info", [])
        missing_info = router_decision.get("missing_info", [])
        if known_info:
            logger.info(
                f"[Agent 1: Router] Known information: {', '.join(known_info[:3])}"
            )
        if missing_info:
            logger.info(
                f"[Agent 1: Router] Missing information: {', '.join(missing_info[:3])}"
            )

        if not router_decision["should_suggest"]:
            logger.info(
                "[Agent 1: Router] Decided NOT to generate suggestions at this time"
            )
            return {
                "suggestions": [],
                "router_decision": router_decision,
                "message": "Router agent determined suggestions are not needed at this time",
            }

        logger.info("[Agent 2: Suggestion] Generating suggestions...")
        suggestions = await _suggestion_agent.generate_suggestions(
            context,
            max_suggestions=max_suggestions,
            known_info=known_info,
            missing_info=missing_info,
        )

        logger.info(f"[Agent 2: Suggestion] Generated {len(suggestions)} suggestions")

        logger.info("-" * 80)
        logger.info(f"OUTPUT SUGGESTIONS ({len(suggestions)} total):")
        for idx, sugg in enumerate(suggestions, 1):
            logger.info(f"  [{idx}] Type: {sugg.get('type', 'N/A')}")
            logger.info(f"       Topic: {sugg.get('topic', sugg.get('text', 'N/A'))}")
            logger.info(f"       Confidence: {sugg.get('confidence', 0):.2f}")
            logger.info(
                f"       Priority: {sugg.get('details', {}).get('priority', 'N/A')}"
            )
            if sugg.get("details", {}).get("possibleConversation"):
                logger.info(
                    f"       Possible Conversation: {sugg['details']['possibleConversation'][:80]}..."
                )
        logger.info("=" * 80)
        logger.info("")

        return {"suggestions": suggestions, "router_decision": router_decision}
    except Exception as e:
        logger.error(f"ERROR processing suggestions: {type(e).__name__}: {str(e)}")
        logger.error(f"Traceback: {repr(e)}")

        fallback_suggestions = get_fallback_suggestions()

        logger.warning("Using fallback suggestions due to error")
        logger.info("-" * 80)
        logger.info(
            f"OUTPUT FALLBACK SUGGESTIONS ({len(fallback_suggestions[:max_suggestions])} total):"
        )
        for idx, sugg in enumerate(fallback_suggestions[:max_suggestions], 1):
            logger.info(f"  [{idx}] Type: {sugg.get('type', 'N/A')}")
            logger.info(f"       Text: {sugg.get('text', 'N/A')}")
        logger.info("=" * 80)
        logger.info("")

        return {
            "suggestions": fallback_suggestions[:max_suggestions],
            "error": str(e),
        }
