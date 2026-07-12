from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Dict, Any
import logging
import json
from openai import OpenAI
from .config import OPENAI_API_KEY, SUGGESTION_MODEL, SUGGESTION_TEMPERATURE
from .auth import enforce_usage_limits
from .persistence import persist_customer_extract_event

logger = logging.getLogger(__name__)


router = APIRouter()


class ExtractCustomerDataRequest(BaseModel):
    conversation_transcript: str
    session_id: str | None = Field(
        None, description="Persist to this session when valid and owned"
    )


class CustomerDataExtractor:
    """Extracts customer information from conversation transcripts using LLM"""

    def __init__(self):
        self.model = SUGGESTION_MODEL
        self.temperature = SUGGESTION_TEMPERATURE
        self.api_key = OPENAI_API_KEY
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None

    SYSTEM_PROMPT = """You are a customer information extraction system for a legal entity in singapore's legal assistance calls.

Your task is to extract specific customer information from conversation transcripts:
- Name: Full name of the customer/caller
- NRIC/Worker's Permit ID: Identification number (e.g., S1234567A, T1234567A, F1234567X, or work permit numbers)
- Address: Full address of the customer
- Purpose of Call: The reason why the customer is calling (e.g., employment dispute, housing issue, contract review, etc.)

SPEAKER LABELS:
- Transcript lines are prefixed Staff: (operator), Customer: (caller), or Unknown:.
- Extract identity and case facts ONLY from Customer: lines.
- Do NOT treat Staff: questions or statements as customer-provided answers (e.g. Staff asking "What is your name?" is not a name).
- Prefer not to extract hard facts from Unknown: lines unless clearly the customer speaking.

IMPORTANT RULES:
1. Only extract information that is EXPLICITLY mentioned by the Customer. Do not infer or guess.
2. If a field is not mentioned, return null for that field.
3. Preserve the exact information as mentioned (e.g., if name is "John Tan", extract "John Tan", not variations)
4. For addresses, extract the complete address if mentioned.
5. For Purpose of Call, extract the main reason the customer is calling in 1-2 sentences.

Return ONLY a valid JSON object with these exact keys:
{
  "name": "string or null",
  "nric_worker_permit_id": "string or null",
  "address": "string or null",
  "purpose_of_call": "string or null"
}

Return JSON only, no markdown, no explanations."""

    USER_PROMPT_TEMPLATE = """Extract customer information from this conversation transcript.
Lines are labeled Staff: / Customer: / Unknown: — use Customer: lines for facts only.

CONVERSATION TRANSCRIPT:
{conversation_transcript}

Return a JSON object with the extracted information. If any field is not mentioned, use null for that field."""

    async def extract(self, conversation_transcript: str) -> Dict[str, Any]:
        """Extract customer data from conversation transcript"""
        if not conversation_transcript or len(conversation_transcript.strip()) < 10:
            return {
                "name": None,
                "nric_worker_permit_id": None,
                "address": None,
                "purpose_of_call": None,
            }

        try:
            user_prompt = self.USER_PROMPT_TEMPLATE.format(
                conversation_transcript=conversation_transcript
            )

            logger.info(
                f"[Customer Data Extractor] Extracting data from transcript ({len(conversation_transcript)} chars)"
            )

            if not self.client:
                raise ValueError("OpenAI API key not configured")

            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
            content = (response.choices[0].message.content or "").strip()

            # Clean up JSON response (handle markdown code blocks if present)
            if content.startswith("```"):
                content = content.strip("`")
                if content.startswith("json"):
                    content = content[4:].lstrip()
                if content.startswith("\n"):
                    content = content[1:]

            # Parse JSON response
            try:
                extracted_data = json.loads(content)
                logger.info(
                    f"[Customer Data Extractor] Successfully extracted: {list(extracted_data.keys())}"
                )
                return {
                    "name": extracted_data.get("name"),
                    "nric_worker_permit_id": extracted_data.get(
                        "nric_worker_permit_id"
                    ),
                    "address": extracted_data.get("address"),
                    "purpose_of_call": extracted_data.get("purpose_of_call"),
                }
            except json.JSONDecodeError as e:
                logger.error(
                    f"[Customer Data Extractor] Failed to parse JSON response: {e}"
                )
                logger.error(f"[Customer Data Extractor] Raw response: {content[:200]}")
                return {
                    "name": None,
                    "nric_worker_permit_id": None,
                    "address": None,
                    "purpose_of_call": None,
                }

        except Exception as e:
            logger.error(
                f"[Customer Data Extractor] Error: {type(e).__name__}: {str(e)}"
            )
            return {
                "name": None,
                "nric_worker_permit_id": None,
                "address": None,
                "purpose_of_call": None,
            }


extractor = CustomerDataExtractor()


@router.post("/extract-customer-data")
async def extract_customer_data(
    req: ExtractCustomerDataRequest,
    user_key: str = Depends(enforce_usage_limits),
) -> Dict[str, Any]:
    """Extract customer information from conversation transcript"""
    try:
        extracted = await extractor.extract(req.conversation_transcript)
        body: dict[str, Any] = {"success": True, "data": extracted}
        persist_customer_extract_event(
            req.session_id,
            user_key,
            req.conversation_transcript,
            True,
            extracted,
        )
        return body
    except Exception as e:
        logger.error(f"[Customer Data Extractor] Endpoint error: {e}")
        empty = {
            "name": None,
            "nric_worker_permit_id": None,
            "address": None,
            "purpose_of_call": None,
        }
        body = {
            "success": False,
            "data": empty,
            "error": str(e),
        }
        persist_customer_extract_event(
            req.session_id,
            user_key,
            req.conversation_transcript,
            False,
            empty,
            error=str(e),
        )
        return body
