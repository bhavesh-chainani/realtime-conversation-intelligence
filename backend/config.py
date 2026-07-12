import os
import pathlib
import json
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True), override=True)

ASSEMBLYAI_API_KEY = (os.getenv("ASSEMBLYAI_API_KEY") or "").strip()
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
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
SUGGESTION_MODEL = CONFIG.get("suggestion_model", "gpt-3.5-turbo")
SUGGESTION_TEMPERATURE = CONFIG.get("suggestion_temperature", 0.3)
SUGGESTION_MAX = CONFIG.get("max_suggestions", 3)

BACKEND_CORS_ORIGINS = [
    origin.strip()
    for origin in (os.getenv("BACKEND_CORS_ORIGINS") or "http://localhost:3000").split(",")
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
ASYNC_JOBS_ENABLED = (
    os.getenv("ASYNC_JOBS_ENABLED") or "false"
).strip().lower() in {"1", "true", "yes", "on"}
INFERENCE_QUEUE_MODE = (
    os.getenv("INFERENCE_QUEUE_MODE") or "poll"
).strip().lower()  # poll | sqs
AWS_SQS_INFERENCE_QUEUE_URL = (os.getenv("AWS_SQS_INFERENCE_QUEUE_URL") or "").strip()
JOB_STORE_BACKEND = (
    os.getenv("JOB_STORE_BACKEND") or "sqlite"
).strip().lower()

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

