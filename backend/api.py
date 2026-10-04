from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import config as cfg
from .assemblyai import ssl_context
from .customer_data_extractor import router as customer_data_router
from .customer_history import router as customer_history_router
from .diarization import nemotron
from .llm import llm_runtime_config
from .stt_relay import issue_ticket
from .stt_relay import router as stt_relay_router
from .suggestions import router as suggest_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load the CA bundle now, not when the first call connects (can take seconds).
    tls_task = asyncio.create_task(ssl_context())
    # A failed load is logged and reported by /ready; calls then arrive without speakers.
    await asyncio.to_thread(nemotron.load_diarizer)
    yield
    tls_task.cancel()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.BACKEND_CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

app.include_router(suggest_router)
app.include_router(customer_data_router)
app.include_router(customer_history_router)
app.include_router(stt_relay_router)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/ready")
async def ready():
    """What is configured and loaded. `ready` means a live call can be transcribed and assisted."""
    llm_configured = bool(llm_runtime_config()["llm_configured"])
    transcription_configured = bool(cfg.ASSEMBLYAI_API_KEY)
    diarization: dict = {"ready": nemotron.get_diarizer() is not None, "mode": cfg.DIARIZATION_MODE}
    if nemotron.load_error():
        diarization["error"] = nemotron.load_error()
    return {
        "ready": llm_configured and transcription_configured,
        "llm_configured": llm_configured,
        "transcription_configured": transcription_configured,
        "diarization": diarization,
        "customer_db_configured": bool(cfg.CUSTOMER_HISTORY_DATABASE_URL),
    }


@app.get("/stt/session")
async def stt_session():
    """A one-time ticket for the live transcription relay (/ws/stt)."""
    if not cfg.ASSEMBLYAI_API_KEY:
        raise HTTPException(status_code=503, detail="Transcription not configured: set ASSEMBLYAI_API_KEY")
    return {"path": "/ws/stt", "ticket": issue_ticket()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.api:app", host="0.0.0.0", port=8000, reload=True)
