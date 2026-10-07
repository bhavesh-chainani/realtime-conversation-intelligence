"""AssemblyAI streaming: the word transcriber behind the STT relay (speakers come from Nemotron)."""

from __future__ import annotations

import asyncio
import json
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlencode

from . import config as cfg

STREAMING_WS_URL = "wss://streaming.assemblyai.com/v3/ws"

_ssl_context: ssl.SSLContext | None = None


async def ssl_context() -> ssl.SSLContext:
    """One shared TLS context, built off the event loop: loading the CA bundle can take seconds
    (e.g. an SSL_CERT_FILE on a slow mounted drive) and must not stall live sessions."""
    global _ssl_context
    if _ssl_context is None:
        _ssl_context = await asyncio.to_thread(ssl.create_default_context)
    return _ssl_context


def loaded_ssl_context() -> ssl.SSLContext | None:
    """The shared TLS context if ssl_context() has already loaded it (startup does), else None."""
    return _ssl_context


def streaming_params(sample_rate: int) -> dict[str, str]:
    """Query params for the v3 streaming WebSocket."""
    # u3 models always format turns, so there is no `format_turns`.
    params = {"sample_rate": str(sample_rate)}
    if cfg.ASSEMBLYAI_KEYTERMS:
        params["keyterms_prompt"] = json.dumps(cfg.ASSEMBLYAI_KEYTERMS)
    if cfg.ASSEMBLYAI_SPEECH_MODEL:
        params["speech_model"] = cfg.ASSEMBLYAI_SPEECH_MODEL
    params.update(cfg.ASSEMBLYAI_STREAM_PARAMS)
    return params


@asynccontextmanager
async def connect(sample_rate: int) -> AsyncIterator[Any]:
    """Server-side streaming session, authenticated with the API key (never sent to the browser)."""
    from websockets.asyncio.client import connect as ws_connect

    async with ws_connect(
        f"{STREAMING_WS_URL}?{urlencode(streaming_params(sample_rate))}",
        additional_headers={"Authorization": cfg.ASSEMBLYAI_API_KEY},
        ssl=await ssl_context(),
        max_size=None,
        open_timeout=60,
    ) as ws:
        yield ws
