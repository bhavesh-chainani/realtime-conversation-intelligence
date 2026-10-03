"""AssemblyAI streaming: temporary tokens and the session settings shared by both STT paths."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl

import httpx

from . import config as cfg

logger = logging.getLogger(__name__)


class StreamingTokenError(Exception):
    pass


_ssl_context: ssl.SSLContext | None = None


async def ssl_context() -> ssl.SSLContext:
    """One shared TLS context, built off the event loop: loading the CA bundle can take seconds
    (e.g. an SSL_CERT_FILE on a slow mounted drive) and must not stall live sessions."""
    global _ssl_context
    if _ssl_context is None:
        _ssl_context = await asyncio.to_thread(ssl.create_default_context)
    return _ssl_context


async def create_streaming_token(expires_in_seconds: int = 300) -> str:
    if not cfg.ASSEMBLYAI_API_KEY:
        raise StreamingTokenError("AssemblyAI API key not configured")

    async with httpx.AsyncClient(timeout=10.0, verify=await ssl_context()) as client:
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


STREAMING_WS_URL = "wss://streaming.assemblyai.com/v3/ws"


def stt_session_config(scenario: str | None, demo: bool | None = None) -> dict:
    """Keyterms, STT prompt, speech model and stream params for a session.

    Raises `demo_cache.ScenarioNotFound` for an unknown scenario in demo mode.
    """
    keyterms = list(cfg.ASSEMBLYAI_KEYTERMS)
    prompt = ""
    speech_model = cfg.ASSEMBLYAI_SPEECH_MODEL
    if (cfg.DEMO_MODE if demo is None else demo) and scenario:
        from .demo_cache import scenario_stt_config

        keyterms, prompt = scenario_stt_config(scenario)
        speech_model = speech_model or cfg.DEMO_SPEECH_MODEL
    # AssemblyAI rejects the whole session if `prompt` is sent to a non-u3 model.
    if not speech_model.startswith("u3"):
        prompt = ""
    return {
        "keyterms": keyterms,
        "prompt": prompt,
        "speech_model": speech_model,
        "stream_params": dict(cfg.ASSEMBLYAI_STREAM_PARAMS),
    }


def streaming_params(sample_rate: int, session: dict) -> dict[str, str]:
    """Query params for the v3 streaming WebSocket (same settings as the browser's direct path)."""
    params = {
        "sample_rate": str(sample_rate),
        "format_turns": "true",
        "speaker_labels": "true",
        "max_speakers": "2",
    }
    if session["keyterms"]:
        params["keyterms_prompt"] = json.dumps(session["keyterms"])
    if session["prompt"]:
        params["prompt"] = session["prompt"]
    if session["speech_model"]:
        params["speech_model"] = session["speech_model"]
    params.update({k: str(v) for k, v in session["stream_params"].items()})
    return params
