from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import config as cfg
from . import issue_guides
from .assemblyai import ssl_context
from .customer_history import router as customer_history_router
from .diarization import nemotron
from .llm import llm_is_configured, warm_up
from .orchestrator import router as assist_router
from .stt_relay import issue_ticket
from .stt_relay import router as stt_relay_router
from .wrapup import router as wrapup_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger(__name__)

# Fire-and-forget tasks, kept referenced until they finish.
_background: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # The CA bundle can take seconds to load: do it now, alongside the diariser, and before the LLM
    # client is first built (it reuses the same context).
    tls_task = asyncio.create_task(ssl_context())
    await asyncio.to_thread(issue_guides.load)
    # A failed load is logged and reported by /ready; calls then arrive without speakers.
    await asyncio.to_thread(nemotron.load_diarizer)
    await tls_task
    _spawn(warm_up())
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.BACKEND_CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    max_age=7200,  # the browser re-checks CORS every 2 h, not every 10 min
)

app.include_router(assist_router)
app.include_router(customer_history_router)
app.include_router(stt_relay_router)
app.include_router(wrapup_router)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/ready")
async def ready():
    """What is configured and loaded. `ready` means a live call can be transcribed and assisted."""
    llm_configured = llm_is_configured()
    transcription_configured = bool(cfg.ASSEMBLYAI_API_KEY)
    # scripts/gpu.sh waits for the text '"diarization":{"ready":true', so "ready" stays the first key.
    diarization: dict = {
        "ready": nemotron.get_diarizer() is not None,
        "mode": cfg.DIARIZATION_MODE,
    }
    if error := nemotron.load_error():
        diarization["error"] = error
    return {
        "ready": llm_configured and transcription_configured,
        "llm_configured": llm_configured,
        "transcription_configured": transcription_configured,
        "diarization": diarization,
        "customer_db_configured": bool(cfg.CUSTOMER_HISTORY_DATABASE_URL),
        "issue_guides": len(issue_guides.guides()),
        "case_store_enabled": cfg.CASE_STORE_ENABLED,
    }


@app.get("/stt/session")
async def stt_session():
    """A one-time ticket for the live transcription relay (/ws/stt)."""
    if not cfg.ASSEMBLYAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Transcription not configured: set ASSEMBLYAI_API_KEY",
        )
    # A call is starting: open the LLM connection now so its first suggestion is not cold.
    _spawn(warm_up())
    return {"path": "/ws/stt", "ticket": issue_ticket()}
