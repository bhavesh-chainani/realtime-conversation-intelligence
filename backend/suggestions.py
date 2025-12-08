from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Any, Dict
import logging
from datetime import datetime
from .router_agent import RouterAgent
from .suggestion_agent import SuggestionAgent
from .prompt_loader import get_fallback_suggestions

router = APIRouter()

# Configure logging for suggestions
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize agents
router_agent = RouterAgent()
suggestion_agent = SuggestionAgent()

class SuggestRequest(BaseModel):
    context: str
    max_suggestions: int = 2

@router.post("/suggest")
async def suggest(req: SuggestRequest) -> Dict[str, Any]:
    # Log incoming request
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("=" * 80)
    logger.info(f"[SUGGESTION REQUEST] {timestamp}")
    logger.info("-" * 80)
    logger.info(f"Input Conversation Transcript ({len(req.context)} chars):")
    logger.info(f"'{req.context}'")
    logger.info("-" * 80)
    
    # TWO-AGENT APPROACH:
    # Agent 1 (Router): Decides if suggestions should be generated
    # Agent 2 (Suggestion): Generates suggestions if router approves
    
    try:
        # Step 1: Router Agent decides if we need suggestions
        logger.info("[Agent 1: Router] Analyzing conversation to decide if suggestions are needed...")
        router_decision = await router_agent.should_get_suggestions(req.context)
        
        logger.info(f"[Agent 1: Router] Decision: should_suggest={router_decision['should_suggest']}, confidence={router_decision['confidence']:.2f}, reason={router_decision['reason']}")
        
        # Log context analysis
        known_info = router_decision.get("known_info", [])
        missing_info = router_decision.get("missing_info", [])
        if known_info:
            logger.info(f"[Agent 1: Router] Known information: {', '.join(known_info[:3])}")
        if missing_info:
            logger.info(f"[Agent 1: Router] Missing information: {', '.join(missing_info[:3])}")
        
        if not router_decision["should_suggest"]:
            logger.info("[Agent 1: Router] Decided NOT to generate suggestions at this time")
            return {
                "suggestions": [],
                "router_decision": router_decision,
                "message": "Router agent determined suggestions are not needed at this time"
            }
        
        # Step 2: Suggestion Agent generates suggestions (only if router approved)
        # Pass context from router agent to avoid redundant questions
        logger.info("[Agent 2: Suggestion] Generating suggestions...")
        suggestions = await suggestion_agent.generate_suggestions(
            req.context, 
            max_suggestions=req.max_suggestions,
            known_info=known_info,
            missing_info=missing_info
        )
        
        logger.info(f"[Agent 2: Suggestion] Generated {len(suggestions)} suggestions")
        
        # Log output suggestions
        logger.info("-" * 80)
        logger.info(f"OUTPUT SUGGESTIONS ({len(suggestions)} total):")
        for idx, sugg in enumerate(suggestions, 1):
            logger.info(f"  [{idx}] Type: {sugg.get('type', 'N/A')}")
            logger.info(f"       Topic: {sugg.get('topic', sugg.get('text', 'N/A'))}")
            logger.info(f"       Confidence: {sugg.get('confidence', 0):.2f}")
            logger.info(f"       Priority: {sugg.get('details', {}).get('priority', 'N/A')}")
            if sugg.get('details', {}).get('possibleConversation'):
                logger.info(f"       Possible Conversation: {sugg['details']['possibleConversation'][:80]}...")
        logger.info("=" * 80)
        logger.info("")
        
        return {
            "suggestions": suggestions,
            "router_decision": router_decision
        }
    except Exception as e:
        logger.error(f"ERROR processing suggestions: {type(e).__name__}: {str(e)}")
        logger.error(f"Traceback: {repr(e)}")
        
        # Load fallback suggestions from external file for easy customization
        fallback_suggestions = get_fallback_suggestions()
        
        logger.warning("Using fallback suggestions due to error")
        logger.info("-" * 80)
        logger.info(f"OUTPUT FALLBACK SUGGESTIONS ({len(fallback_suggestions[:req.max_suggestions])} total):")
        for idx, sugg in enumerate(fallback_suggestions[:req.max_suggestions], 1):
            logger.info(f"  [{idx}] Type: {sugg.get('type', 'N/A')}")
            logger.info(f"       Text: {sugg.get('text', 'N/A')}")
        logger.info("=" * 80)
        logger.info("")
        
        return {"suggestions": fallback_suggestions[:req.max_suggestions], "error": str(e)}