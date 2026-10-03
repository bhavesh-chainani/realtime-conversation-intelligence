"""Core suggestion inference (sync API + worker)."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from . import config as cfg
from .llm import get_router_model, get_suggestion_model
from .prompt_loader import format_customer_record, get_fallback_suggestions
from .router_agent import RouterAgent
from .suggestion_agent import SuggestionAgent
from .suggestion_fast import generate_fast

logger = logging.getLogger(__name__)

_router_agent = RouterAgent()
_suggestion_agent = SuggestionAgent()


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


async def compute_suggestions(
    context: str,
    max_suggestions: int = 2,
    customer_profile: Optional[Dict[str, Any]] = None,
    customer_cases: Optional[List[Dict[str, Any]]] = None,
    pipeline: Optional[str] = None,
) -> Dict[str, Any]:
    pipeline = (pipeline or cfg.SUGGESTION_PIPELINE or "router").lower()
    started = time.perf_counter()
    logger.info(
        "[SUGGESTION REQUEST] pipeline=%s chars=%s cases=%s",
        pipeline,
        len(context),
        len(customer_cases or []),
    )

    try:
        if pipeline == "single":
            body = await generate_fast(
                context,
                max_suggestions=max_suggestions,
                customer_profile=customer_profile,
                customer_cases=customer_cases,
            )
            body["timings"] = {
                **body.get("timings", {}),
                "total_ms": _elapsed_ms(started),
                "pipeline": pipeline,
            }
            logger.info(
                "[Suggestion] single-call produced %s suggestions in %sms (llm %sms)",
                len(body["suggestions"]),
                body["timings"]["total_ms"],
                body["timings"].get("llm_ms"),
            )
            return body

        router_started = time.perf_counter()
        router_decision = await _router_agent.should_get_suggestions(context)
        router_ms = _elapsed_ms(router_started)
        logger.info(
            "[Agent 1: Router] should_suggest=%s confidence=%.2f reason=%s (%sms)",
            router_decision["should_suggest"],
            router_decision["confidence"],
            router_decision["reason"],
            router_ms,
        )

        timings: Dict[str, Any] = {
            "router_ms": router_ms,
            "pipeline": pipeline,
            "model": get_router_model(),
        }
        if not router_decision["should_suggest"]:
            timings["total_ms"] = _elapsed_ms(started)
            return {
                "suggestions": [],
                "router_decision": router_decision,
                "message": "Router agent determined suggestions are not needed at this time",
                "timings": timings,
            }

        suggest_started = time.perf_counter()
        suggestions = await _suggestion_agent.generate_suggestions(
            context,
            max_suggestions=max_suggestions,
            known_info=router_decision.get("known_info", []),
            missing_info=router_decision.get("missing_info", []),
            customer_record=format_customer_record(customer_profile, customer_cases),
        )
        timings.update(
            llm_ms=_elapsed_ms(suggest_started),
            total_ms=_elapsed_ms(started),
            model=get_suggestion_model(),
        )
        logger.info(
            "[Agent 2: Suggestion] Generated %s suggestions (%sms total)",
            len(suggestions),
            timings["total_ms"],
        )
        return {
            "suggestions": suggestions,
            "router_decision": router_decision,
            "timings": timings,
        }
    except Exception as e:
        logger.error(f"ERROR processing suggestions: {type(e).__name__}: {str(e)}")
        logger.warning("Using fallback suggestions due to error")
        return {
            "suggestions": get_fallback_suggestions()[:max_suggestions],
            "error": str(e) or type(e).__name__,
            "fallback": True,
            "timings": {"total_ms": _elapsed_ms(started), "pipeline": pipeline},
        }
