"""AssemblyAI streaming token helper shared by the token endpoint and demo preflight."""

from __future__ import annotations

import logging

import httpx

from . import config as cfg

logger = logging.getLogger(__name__)


class StreamingTokenError(Exception):
    pass


async def create_streaming_token(expires_in_seconds: int = 300) -> str:
    if not cfg.ASSEMBLYAI_API_KEY:
        raise StreamingTokenError("AssemblyAI API key not configured")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            "https://streaming.assemblyai.com/v3/token",
            params={"expires_in_seconds": expires_in_seconds},
            headers={"Authorization": cfg.ASSEMBLYAI_API_KEY},
        )
    if resp.status_code >= 400:
        logger.error(
            "Failed to create AssemblyAI temporary token: status=%s body=%s",
            resp.status_code,
            resp.text[:300],
        )
        raise StreamingTokenError("Failed to generate streaming token")

    token = resp.json().get("token")
    if not token:
        raise StreamingTokenError("Token missing from AssemblyAI response")
    return token
