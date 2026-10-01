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
from .assemblyai import StreamingTokenError, create_streaming_token
from .async_jobs_router import router as async_jobs_router
from .auth import enforce_usage_limits, require_api_auth
from .config import ASYNC_JOBS_ENABLED, BACKEND_CORS_ORIGINS, DEMO_MODE
from .call_summary import router as call_summary_router
from .customer_data_extractor import router as customer_data_router
from .customer_history import router as customer_history_router
from .http_middleware import RequestContextMiddleware
from .llm import llm_runtime_config
from .sessions_api import router as sessions_router
from .suggestions import router as suggest_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    prewarm_task = None
    if DEMO_MODE:
        from .demo_api import prewarm

        # Background so startup isn't blocked; opens LLM keep-alive connections early.
        prewarm_task = asyncio.create_task(prewarm())
    yield
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
if ASYNC_JOBS_ENABLED:
    app.include_router(async_jobs_router)
if DEMO_MODE:
    from .demo_api import router as demo_router

    app.include_router(demo_router)


@app.get("/health")
async def health():
    from . import config as cfg

    out: dict = {"ok": True}
    if cfg.APP_VERSION:
        out["version"] = cfg.APP_VERSION
    if cfg.GIT_SHA:
        out["git_sha"] = cfg.GIT_SHA
    return out


@app.get("/ready")
async def ready():
    """Deep readiness: optional strict mode (503 if LLM/STT keys missing)."""
    from . import config as cfg

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
        "sql_lookup_model_configured": bool(llm_cfg["sql_lookup_model_configured"]),
        "customer_history_configured": customer_history_configured,
        "assemblyai_configured": bool(cfg.ASSEMBLYAI_API_KEY),
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
    from . import config as cfg

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


@app.get("/assemblyai-token")
async def assemblyai_token(
    scenario: str | None = None, _: str = Depends(enforce_usage_limits)
):
    keyterms = list(cfg.ASSEMBLYAI_KEYTERMS)
    prompt = ""
    speech_model = cfg.ASSEMBLYAI_SPEECH_MODEL
    if DEMO_MODE and scenario:
        from .demo_cache import ScenarioNotFound, scenario_stt_config

        try:
            keyterms, prompt = scenario_stt_config(scenario)
        except ScenarioNotFound:
            raise HTTPException(status_code=404, detail="Unknown scenario")
        speech_model = speech_model or cfg.DEMO_SPEECH_MODEL
    # AssemblyAI rejects the whole session if `prompt` is sent to a non-u3 model.
    if not speech_model.startswith("u3"):
        prompt = ""

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
    if keyterms:
        out["keyterms_prompt"] = keyterms
    if prompt:
        out["prompt"] = prompt
    if speech_model:
        out["speech_model"] = speech_model
    return out


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.api:app", host="0.0.0.0", port=8000, reload=True)
