from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from . import config as cfg
from .assemblyai import StreamingTokenError, create_streaming_token, ssl_context, stt_session_config
from .config import BACKEND_CORS_ORIGINS
from .call_summary import router as call_summary_router
from .customer_data_extractor import router as customer_data_router
from .customer_history import router as customer_history_router
from .diarization import nemotron
from .llm import llm_runtime_config
from .stt_relay import issue_ticket
from .stt_relay import router as stt_relay_router
from .suggestions import router as suggest_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load the CA bundle now, not when the first call connects (can take seconds).
    tls_task = asyncio.create_task(ssl_context())
    if cfg.DIARIZATION_BACKEND == "nemotron":
        await asyncio.to_thread(nemotron.load_diarizer)
    yield
    tls_task.cancel()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=BACKEND_CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

app.include_router(suggest_router)
app.include_router(call_summary_router)
app.include_router(customer_data_router)
app.include_router(customer_history_router)
app.include_router(stt_relay_router)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/ready")
async def ready():
    """What is configured and loaded."""
    llm_cfg = llm_runtime_config()
    customer_history_configured = bool(cfg.CUSTOMER_HISTORY_DATABASE_URL)
    detail = {
        "ready": True,
        "llm_configured": bool(llm_cfg["llm_configured"]),
        "llm_api_key_loaded": bool(llm_cfg["llm_api_key_loaded"]),
        "llm_base_url_configured": bool(llm_cfg["llm_base_url_configured"]),
        "router_model_configured": bool(llm_cfg["router_model_configured"]),
        "suggestion_model_configured": bool(llm_cfg["suggestion_model_configured"]),
        "extraction_model_configured": bool(llm_cfg["extraction_model_configured"]),
        "customer_history_configured": customer_history_configured,
        "assemblyai_configured": bool(cfg.ASSEMBLYAI_API_KEY),
        "diarization": diarization_status(),
    }
    return detail


def diarization_status() -> dict:
    """Which diariser live calls use, and whether it is loaded."""
    if cfg.DIARIZATION_BACKEND != "nemotron":
        return {"backend": "assemblyai", "ready": True}
    out: dict = {"backend": "nemotron", "ready": nemotron.get_diarizer() is not None, "mode": cfg.DIARIZATION_MODE}
    if nemotron.load_error():
        out["error"] = nemotron.load_error()
    return out


@app.get("/assemblyai-token")
async def assemblyai_token():
    """How the browser should start live transcription.

    With Nemotron diarisation loaded: a one-time ticket for the backend relay (/ws/stt).
    Otherwise: a temporary AssemblyAI token plus session settings for the direct path.
    """
    session = stt_session_config()

    if cfg.DIARIZATION_BACKEND == "nemotron" and nemotron.get_diarizer() is not None:
        return {"relay": {"path": "/ws/stt", "ticket": issue_ticket()}, "diarization": "nemotron"}

    try:
        token = await create_streaming_token()
    except StreamingTokenError as exc:
        status = 500 if "not configured" in str(exc) else 502
        raise HTTPException(status_code=status, detail=str(exc))
    except Exception as exc:
        logger.exception(
            "Unexpected error generating AssemblyAI temporary token: %s", exc
        )
        raise HTTPException(
            status_code=502, detail="Unable to generate streaming token"
        )

    out: dict = {"token": token}
    if session["keyterms"]:
        out["keyterms_prompt"] = session["keyterms"]
    if session["prompt"]:
        out["prompt"] = session["prompt"]
    if session["speech_model"]:
        out["speech_model"] = session["speech_model"]
    if session["stream_params"]:
        out["stream_params"] = session["stream_params"]
    return out


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.api:app", host="0.0.0.0", port=8000, reload=True)
