from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from .logging_config import setup_logging

setup_logging()

from . import config as cfg
from .assemblyai import StreamingTokenError, create_streaming_token, ssl_context, stt_session_config
from .async_jobs_router import router as async_jobs_router
from .auth import enforce_usage_limits, require_api_auth
from .config import ASYNC_JOBS_ENABLED, BACKEND_CORS_ORIGINS, DEMO_MODE
from .call_summary import router as call_summary_router
from .customer_data_extractor import router as customer_data_router
from .customer_history import router as customer_history_router
from .diarization import nemotron
from .http_middleware import RequestContextMiddleware
from .llm import llm_runtime_config
from .sessions_api import router as sessions_router
from .stt_relay import issue_ticket
from .stt_relay import router as stt_relay_router
from .suggestions import router as suggest_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    prewarm_task = None
    # Load the CA bundle now, not when the first call connects (can take seconds).
    tls_task = asyncio.create_task(ssl_context())
    if cfg.DIARIZATION_BACKEND == "nemotron":
        await asyncio.to_thread(nemotron.load_diarizer)
    if DEMO_MODE:
        from .demo_api import prewarm

        # Background so startup isn't blocked; opens LLM keep-alive connections early.
        prewarm_task = asyncio.create_task(prewarm())
    yield
    tls_task.cancel()
    if prewarm_task and not prewarm_task.done():
        prewarm_task.cancel()


app = FastAPI(lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

app.include_router(suggest_router)
app.include_router(call_summary_router)
app.include_router(customer_data_router)
app.include_router(customer_history_router)
app.include_router(sessions_router)
app.include_router(stt_relay_router)
if ASYNC_JOBS_ENABLED:
    app.include_router(async_jobs_router)
if DEMO_MODE:
    from .demo_api import router as demo_router

    app.include_router(demo_router)


@app.get("/health")
async def health():
    out: dict = {"ok": True}
    if cfg.APP_VERSION:
        out["version"] = cfg.APP_VERSION
    if cfg.GIT_SHA:
        out["git_sha"] = cfg.GIT_SHA
    return out


@app.get("/ready")
async def ready():
    """Deep readiness: optional strict mode (503 if LLM/STT keys missing)."""
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
        "version": cfg.APP_VERSION,
    }
    if cfg.GIT_SHA:
        detail["git_sha"] = cfg.GIT_SHA
    if cfg.STRICT_READINESS:
        if not detail["llm_configured"] or not detail["assemblyai_configured"]:
            detail["ready"] = False
            raise HTTPException(status_code=503, detail=detail)
    return detail


@app.get("/metrics")
async def metrics(authorization: str | None = Header(None)):
    if not cfg.METRICS_ENABLED:
        raise HTTPException(status_code=404, detail="metrics disabled")
    if cfg.METRICS_TOKEN:
        expected = f"Bearer {cfg.METRICS_TOKEN}"
        if (authorization or "").strip() != expected:
            raise HTTPException(status_code=401, detail="unauthorized")
    body = generate_latest()
    return Response(content=body, media_type=CONTENT_TYPE_LATEST)


@app.get("/config")
async def config(_: str = Depends(require_api_auth)):
    from .config import (
        ASSEMBLYAI_API_KEY,
        ASSEMBLYAI_KEYTERMS,
        CUSTOMER_HISTORY_DATABASE_URL,
        CUSTOMER_HISTORY_VIEW,
        REQUIRE_API_AUTH,
    )

    return {
        "assemblyai_key_loaded": bool(ASSEMBLYAI_API_KEY),
        "customer_history_configured": bool(CUSTOMER_HISTORY_DATABASE_URL),
        "customer_history_view": CUSTOMER_HISTORY_VIEW,
        "assemblyai_keyterms_count": len(ASSEMBLYAI_KEYTERMS),
        "require_api_auth": REQUIRE_API_AUTH,
        **llm_runtime_config(),
    }


@app.get("/limits")
async def limits(_: str = Depends(require_api_auth)):
    from .config import DAILY_REQUEST_QUOTA, RATE_LIMIT_PER_MINUTE

    return {
        "rate_limit_per_minute": RATE_LIMIT_PER_MINUTE,
        "daily_request_quota": DAILY_REQUEST_QUOTA,
    }


def diarization_status() -> dict:
    """Which diariser live calls use, and whether it is loaded."""
    if cfg.DIARIZATION_BACKEND != "nemotron":
        return {"backend": "assemblyai", "ready": True}
    out: dict = {"backend": "nemotron", "ready": nemotron.get_diarizer() is not None, "mode": cfg.DIARIZATION_MODE}
    if nemotron.load_error():
        out["error"] = nemotron.load_error()
    return out


@app.get("/assemblyai-token")
async def assemblyai_token(
    scenario: str | None = None, _: str = Depends(enforce_usage_limits)
):
    """How the browser should start live transcription.

    With Nemotron diarisation loaded: a one-time ticket for the backend relay (/ws/stt).
    Otherwise: a temporary AssemblyAI token plus session settings for the direct path.
    """
    from .demo_cache import ScenarioNotFound

    try:
        session = stt_session_config(scenario)
    except ScenarioNotFound:
        raise HTTPException(status_code=404, detail="Unknown scenario")

    if cfg.DIARIZATION_BACKEND == "nemotron" and nemotron.get_diarizer() is not None:
        return {"relay": {"path": "/ws/stt", "ticket": issue_ticket(scenario)}, "diarization": "nemotron"}

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
