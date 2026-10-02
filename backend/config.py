import json
import os
import pathlib

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=True)

ASSEMBLYAI_API_KEY = (os.getenv("ASSEMBLYAI_API_KEY") or "").strip()

# LiteLLM/OpenAI-compatible proxy configuration for all LLM tasks.
LLM_API_KEY = (os.getenv("LLM_API_KEY") or "").strip()
LLM_BASE_URL = (os.getenv("LLM_BASE_URL") or "").strip()
LLM_TIMEOUT_SECONDS = float((os.getenv("LLM_TIMEOUT_SECONDS") or "20").strip())

API_AUTH_TOKEN = (os.getenv("API_AUTH_TOKEN") or "").strip()
AUTH_JWKS_URL = (os.getenv("AUTH_JWKS_URL") or "").strip()
AUTH_ISSUER = (os.getenv("AUTH_ISSUER") or "").strip()
AUTH_AUDIENCE = (os.getenv("AUTH_AUDIENCE") or "").strip()

CONFIG_PATH = pathlib.Path(__file__).parent.parent / "config.json"


def load_config_json():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


CONFIG = load_config_json()

# Per-task model settings with config.json fallback.
ROUTER_MODEL = (
    os.getenv("ROUTER_MODEL")
    or CONFIG.get("router_model")
    or CONFIG.get("suggestion_model")
    or "gpt-4o-mini"
)
SUGGESTION_MODEL = (
    os.getenv("SUGGESTION_MODEL") or CONFIG.get("suggestion_model") or ROUTER_MODEL
)
EXTRACTION_MODEL = (
    os.getenv("EXTRACTION_MODEL") or CONFIG.get("extraction_model") or SUGGESTION_MODEL
)
SQL_LOOKUP_MODEL = (
    os.getenv("SQL_LOOKUP_MODEL") or CONFIG.get("sql_lookup_model") or EXTRACTION_MODEL
)
SUGGESTION_TEMPERATURE = float(
    os.getenv("SUGGESTION_TEMPERATURE") or CONFIG.get("suggestion_temperature") or 0.3
)
SUGGESTION_MAX = int(os.getenv("MAX_SUGGESTIONS") or CONFIG.get("max_suggestions") or 3)

BACKEND_CORS_ORIGINS = [
    origin.strip()
    for origin in (os.getenv("BACKEND_CORS_ORIGINS") or "http://localhost:3000").split(
        ","
    )
    if origin.strip()
]
REQUIRE_API_AUTH = (os.getenv("REQUIRE_API_AUTH") or "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
RATE_LIMIT_PER_MINUTE = int((os.getenv("RATE_LIMIT_PER_MINUTE") or "30").strip())
DAILY_REQUEST_QUOTA = int((os.getenv("DAILY_REQUEST_QUOTA") or "2000").strip())

# Streaming v3 uses query param `keyterms_prompt` (JSON array string), not legacy `word_boost`.
_raw_keyterms = CONFIG.get("assemblyai_keyterms") or []
if not isinstance(_raw_keyterms, list):
    _raw_keyterms = []
ASSEMBLYAI_KEYTERMS = [str(x).strip() for x in _raw_keyterms if str(x).strip()][:100]

# Session persistence: sqlite (local/default), dynamodb (AWS), none (disabled)
_repo_root = pathlib.Path(__file__).parent.parent
STORAGE_BACKEND = (os.getenv("STORAGE_BACKEND") or "sqlite").strip().lower()
SQLITE_DB_PATH = pathlib.Path(
    os.getenv("SQLITE_DB_PATH") or (_repo_root / "data" / "sessions.db")
)
DYNAMODB_CONVERSATIONS_TABLE = (os.getenv("DYNAMODB_CONVERSATIONS_TABLE") or "").strip()
AWS_REGION = (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "").strip()

# Async inference jobs (Day 5)
ASYNC_JOBS_ENABLED = (os.getenv("ASYNC_JOBS_ENABLED") or "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
INFERENCE_QUEUE_MODE = (
    (os.getenv("INFERENCE_QUEUE_MODE") or "poll").strip().lower()
)  # poll | sqs
AWS_SQS_INFERENCE_QUEUE_URL = (os.getenv("AWS_SQS_INFERENCE_QUEUE_URL") or "").strip()
JOB_STORE_BACKEND = (os.getenv("JOB_STORE_BACKEND") or "sqlite").strip().lower()

# Customer history lookup: curated Postgres view, read-only only.
CUSTOMER_HISTORY_DATABASE_URL = (
    os.getenv("CUSTOMER_HISTORY_DATABASE_URL") or ""
).strip()
CUSTOMER_HISTORY_VIEW = (
    os.getenv("CUSTOMER_HISTORY_VIEW") or "public.customer_history_view"
).strip()
CUSTOMER_HISTORY_QUERY_TIMEOUT_MS = int(
    (os.getenv("CUSTOMER_HISTORY_QUERY_TIMEOUT_MS") or "2500").strip()
)
CUSTOMER_HISTORY_MAX_ROWS = int(
    (os.getenv("CUSTOMER_HISTORY_MAX_ROWS") or "10").strip()
)

# Day 6–8: observability & ops
LOG_JSON = (os.getenv("LOG_JSON") or "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LOG_LEVEL = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
APP_VERSION = (os.getenv("APP_VERSION") or "dev").strip()
GIT_SHA = (os.getenv("GIT_SHA") or "").strip()
METRICS_ENABLED = (os.getenv("METRICS_ENABLED") or "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
STRICT_READINESS = (os.getenv("STRICT_READINESS") or "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
METRICS_TOKEN = (os.getenv("METRICS_TOKEN") or "").strip()

# LLM latency controls
LLM_MAX_RETRIES = int((os.getenv("LLM_MAX_RETRIES") or "2").strip())
# Optional reasoning effort for GPT-5-family models (e.g. none|minimal|low). Empty = provider default.
LLM_REASONING_EFFORT = (os.getenv("LLM_REASONING_EFFORT") or "").strip()
SUGGESTION_TIMEOUT_SECONDS = float(
    (os.getenv("SUGGESTION_TIMEOUT_SECONDS") or str(LLM_TIMEOUT_SECONDS)).strip()
)
SUGGESTION_MAX_TOKENS = int((os.getenv("SUGGESTION_MAX_TOKENS") or "900").strip())

# Scripted live demo (script-guided diarisation, warm cache, prewarm endpoints)
DEMO_MODE = (os.getenv("DEMO_MODE") or "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DEMO_SCRIPTS_DIR = pathlib.Path(
    os.getenv("DEMO_SCRIPTS_DIR") or (_repo_root / "demo" / "scripts")
)
DEMO_CACHE_DIR = pathlib.Path(
    os.getenv("DEMO_CACHE_DIR") or (_repo_root / "data" / "demo_cache")
)
# single = one merged LLM call (fast); router = router agent + suggestion agent.
SUGGESTION_PIPELINE = (
    (os.getenv("SUGGESTION_PIPELINE") or ("single" if DEMO_MODE else "router"))
    .strip()
    .lower()
)

# Optional extra columns selected from the customer history view (e.g. "address").
CUSTOMER_HISTORY_EXTRA_COLUMNS = [
    col.strip()
    for col in (os.getenv("CUSTOMER_HISTORY_EXTRA_COLUMNS") or "").split(",")
    if col.strip()
]

# Optional AssemblyAI streaming speech model (sent as `speech_model`); empty = server default.
ASSEMBLYAI_SPEECH_MODEL = str(CONFIG.get("assemblyai_speech_model") or "").strip()

# Extra streaming query params, e.g. turn detection. Shorter end-of-turn silence makes finished
# lines appear sooner (measured ~1.0s vs ~1.6s with u3-rt-pro) without hurting accuracy.
_raw_stream_params = CONFIG.get("assemblyai_stream_params") or {}
ASSEMBLYAI_STREAM_PARAMS = (
    {str(k): str(v) for k, v in _raw_stream_params.items()}
    if isinstance(_raw_stream_params, dict)
    else {}
)
# Speech model used for scripted demo sessions; `prompt` (STT context) requires a u3 Pro model.
DEMO_SPEECH_MODEL = (os.getenv("DEMO_SPEECH_MODEL") or "u3-rt-pro").strip()
