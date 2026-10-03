from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from .auth import enforce_usage_limits
from .config import SUGGESTION_TEMPERATURE
from .llm import get_extraction_model, get_llm_client, llm_extra_params, strip_code_fences
from .persistence import persist_customer_extract_event
from .quick_entities import NRIC_PATTERN, extract_nric_from_transcript
from .text_guard import has_foreign_script

logger = logging.getLogger(__name__)

router = APIRouter()

EXTRACTION_FIELDS = (
    "name",
    "nric_worker_permit_id",
    "address",
    "purpose_of_call",
)
PLACEHOLDER_VALUES = {
    "",
    "n/a",
    "na",
    "none",
    "null",
    "nil",
    "unknown",
    "not mentioned",
    "not provided",
    "not available",
    "not given",
    "not stated",
    "unspecified",
}


class ExtractCustomerDataRequest(BaseModel):
    conversation_transcript: str
    session_id: str | None = Field(
        None, description="Persist to this session when valid and owned"
    )


class CustomerDataExtractor:
    """Extracts customer information from conversation transcripts using LLM."""

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
- Prefer not to extract hard facts from Unknown: lines unless it is very clear they are customer statements.

IMPORTANT RULES:
1. Only extract information that is EXPLICITLY mentioned by the Customer. Do not infer or guess.
2. If a field is not mentioned, return null for that field.
3. Preserve the exact information as mentioned, except normalize obvious formatting issues like accidental extra spaces.
4. For addresses, extract the complete address if mentioned.
5. For Purpose of Call, extract the main reason the customer is calling in 1-2 concise sentences.
6. Never copy Staff prompts or placeholders like N/A, unknown, not provided, or null into the output.
7. Prefer null over guessing.

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

    def __init__(self):
        self.temperature = SUGGESTION_TEMPERATURE

    async def extract(self, conversation_transcript: str) -> dict[str, Any]:
        """Extract and normalize customer data from a conversation transcript."""
        if not conversation_transcript or len(conversation_transcript.strip()) < 10:
            data = self._empty_customer_data()
            return self._build_response(
                success=True,
                status="empty",
                data=data,
                message="No customer details captured yet from the current transcript.",
            )

        try:
            user_prompt = self.USER_PROMPT_TEMPLATE.format(
                conversation_transcript=conversation_transcript
            )
            logger.info(
                "[Customer Data Extractor] Extracting data from transcript (%s chars)",
                len(conversation_transcript),
            )

            client = get_llm_client()
            if not client:
                raise ValueError("LLM client is not configured")

            started = time.perf_counter()
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=get_extraction_model(),
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                **llm_extra_params(),
            )
            llm_ms = round((time.perf_counter() - started) * 1000, 1)
            content = strip_code_fences(
                (response.choices[0].message.content or "").strip()
            )
            extracted_data = json.loads(content)
            normalized = self._normalize_payload(extracted_data)
            normalized["nric_worker_permit_id"] = self._reconcile_id(
                normalized.get("nric_worker_permit_id"), conversation_transcript
            )
            # Drop any field the model wrote partly in another script; staff see it blank instead.
            for field, value in normalized.items():
                if value and has_foreign_script(value):
                    normalized[field] = None
            status = self._status_for_data(normalized)

            logger.info(
                "[Customer Data Extractor] Extracted status=%s captured=%s",
                status,
                [field for field in EXTRACTION_FIELDS if normalized.get(field)],
            )
            body = self._build_response(
                success=True,
                status=status,
                data=normalized,
                message=self._message_for_status(status),
            )
            body["timings"] = {"llm_ms": llm_ms, "model": get_extraction_model()}
            return body
        except json.JSONDecodeError as exc:
            logger.error("[Customer Data Extractor] Failed to parse JSON response: %s", exc)
            return self._error_response("LLM returned invalid JSON for customer extraction.")
        except Exception as exc:
            logger.error(
                "[Customer Data Extractor] Error: %s: %s",
                type(exc).__name__,
                str(exc),
            )
            return self._error_response("Customer data extraction failed.", error=str(exc))

    def _reconcile_id(self, llm_value: str | None, transcript: str) -> str | None:
        """Prefer a well-formed ID; fall back to a regex hit on Customer: lines."""
        if llm_value and NRIC_PATTERN.fullmatch(llm_value.lower()):
            return llm_value
        return extract_nric_from_transcript(transcript) or llm_value

    def _empty_customer_data(self) -> dict[str, None]:
        return {field: None for field in EXTRACTION_FIELDS}

    def _normalize_payload(self, payload: Any) -> dict[str, str | None]:
        raw = payload if isinstance(payload, dict) else {}
        return {
            field: self._normalize_value(field, raw.get(field))
            for field in EXTRACTION_FIELDS
        }

    def _normalize_value(self, field: str, value: Any) -> str | None:
        if value is None:
            return None

        text = str(value).strip()
        if not text:
            return None

        if field == "nric_worker_permit_id":
            text = re.sub(r"\s+", "", text).upper()
        else:
            text = re.sub(r"\s+", " ", text)

        if text.lower() in PLACEHOLDER_VALUES:
            return None

        if field == "name" and text.lower() in {"customer", "caller", "unknown customer"}:
            return None

        return text

    def _status_for_data(self, data: dict[str, str | None]) -> str:
        captured_count = sum(1 for field in EXTRACTION_FIELDS if data.get(field))
        if captured_count == 0:
            return "empty"
        if captured_count == len(EXTRACTION_FIELDS):
            return "complete"
        return "partial"

    def _build_meta(self, data: dict[str, str | None]) -> dict[str, Any]:
        captured_fields = [field for field in EXTRACTION_FIELDS if data.get(field)]
        missing_fields = [field for field in EXTRACTION_FIELDS if not data.get(field)]
        return {
            "captured_fields": captured_fields,
            "missing_fields": missing_fields,
            "captured_count": len(captured_fields),
            "total_fields": len(EXTRACTION_FIELDS),
            "history_lookup_ready": bool(
                data.get("name") or data.get("nric_worker_permit_id")
            ),
        }

    def _message_for_status(self, status: str) -> str:
        if status == "empty":
            return "No customer details captured yet from the current transcript."
        if status == "complete":
            return "Customer details captured and ready for staff review."
        return "Partial customer details captured. Staff should review and complete any missing fields."

    def _build_response(
        self,
        *,
        success: bool,
        status: str,
        data: dict[str, str | None],
        message: str,
        error: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "success": success,
            "status": status,
            "message": message,
            "data": data,
            "meta": self._build_meta(data),
        }
        if error:
            body["error"] = error
        return body

    def _error_response(self, message: str, error: str | None = None) -> dict[str, Any]:
        return self._build_response(
            success=False,
            status="error",
            data=self._empty_customer_data(),
            message=message,
            error=error,
        )



extractor = CustomerDataExtractor()


@router.post("/extract-customer-data")
async def extract_customer_data(
    req: ExtractCustomerDataRequest,
    background_tasks: BackgroundTasks,
    user_key: str = Depends(enforce_usage_limits),
) -> dict[str, Any]:
    """Extract customer information from conversation transcript."""
    try:
        body = await extractor.extract(req.conversation_transcript)
        err = body.get("error") if isinstance(body.get("error"), str) else None
        background_tasks.add_task(
            persist_customer_extract_event,
            req.session_id,
            user_key,
            req.conversation_transcript,
            body,
            error=err,
        )
        return body
    except Exception as exc:
        logger.error("[Customer Data Extractor] Endpoint error: %s", exc)
        body = extractor._error_response(
            "Customer data extraction failed.", error=str(exc)
        )
        persist_customer_extract_event(
            req.session_id,
            user_key,
            req.conversation_transcript,
            body,
            error=str(exc),
        )
        return body
