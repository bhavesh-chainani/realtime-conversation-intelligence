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

_repo_root = pathlib.Path(__file__).parent.parent
CONFIG_PATH = _repo_root / "config.json"


def load_config_json():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


CONFIG = load_config_json()

# Per-task model settings with config.json fallback.
SUGGESTION_MODEL = (
    os.getenv("SUGGESTION_MODEL") or CONFIG.get("suggestion_model") or "gpt-4o-mini"
)
EXTRACTION_MODEL = (
    os.getenv("EXTRACTION_MODEL") or CONFIG.get("extraction_model") or SUGGESTION_MODEL
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
# Streaming v3 uses query param `keyterms_prompt` (JSON array string), not legacy `word_boost`.
_raw_keyterms = CONFIG.get("assemblyai_keyterms") or []
if not isinstance(_raw_keyterms, list):
    _raw_keyterms = []
ASSEMBLYAI_KEYTERMS = [str(x).strip() for x in _raw_keyterms if str(x).strip()][:100]

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

# LLM latency controls
LLM_MAX_RETRIES = int((os.getenv("LLM_MAX_RETRIES") or "2").strip())
# Optional reasoning effort for GPT-5-family models (e.g. none|minimal|low). Empty = provider default.
LLM_REASONING_EFFORT = (os.getenv("LLM_REASONING_EFFORT") or "").strip()
SUGGESTION_TIMEOUT_SECONDS = float(
    (os.getenv("SUGGESTION_TIMEOUT_SECONDS") or str(LLM_TIMEOUT_SECONDS)).strip()
)
SUGGESTION_MAX_TOKENS = int((os.getenv("SUGGESTION_MAX_TOKENS") or "900").strip())

# Optional extra columns selected from the customer history view (e.g. "address").
CUSTOMER_HISTORY_EXTRA_COLUMNS = [
    col.strip()
    for col in (os.getenv("CUSTOMER_HISTORY_EXTRA_COLUMNS") or "").split(",")
    if col.strip()
]

# AssemblyAI streaming speech model (sent as `speech_model`); empty = server default. config.json sets
# u3-rt-pro: the standard model misheard spoken NRICs ("S1234567A" as "S124567A"), breaking the lookup.
ASSEMBLYAI_SPEECH_MODEL = str(CONFIG.get("assemblyai_speech_model") or "").strip()

# Extra streaming query params, e.g. turn detection. Shorter end-of-turn silence makes finished
# lines appear sooner (measured ~1.0s vs ~1.6s with u3-rt-pro) without hurting accuracy.
_raw_stream_params = CONFIG.get("assemblyai_stream_params") or {}
ASSEMBLYAI_STREAM_PARAMS = (
    {str(k): str(v) for k, v in _raw_stream_params.items()}
    if isinstance(_raw_stream_params, dict)
    else {}
)

# Speaker diarisation: the backend relay (/ws/stt) sends the call audio to AssemblyAI for the words
# and to NVIDIA Nemotron 3 Diarization for who said each word (install requirements-diarization.txt).
# If the model fails to load, turns arrive without speakers and the operator assigns roles.
_default_diar_model = _repo_root / "data" / "models" / "Nemotron-3-Diarization"
DIARIZATION_MODEL = (
    os.getenv("DIARIZATION_MODEL")
    or (str(_default_diar_model) if _default_diar_model.exists() else "nvidia/Nemotron-3-Diarization")
).strip()
DIARIZATION_DEVICE = (os.getenv("DIARIZATION_DEVICE") or "cpu").strip().lower()
_diar_on_gpu = DIARIZATION_DEVICE.startswith("cuda")
# Streaming chunk: low_latency (1.04 s), very_low_latency (0.64 s), ultra_low_latency (0.32 s), or
# "<chunk>x<right_context>" in 80 ms frames. A laptop CPU cannot keep up with 1.04 s; 40x4 (3.5 s) can.
DIARIZATION_MODE = (os.getenv("DIARIZATION_MODE") or ("low_latency" if _diar_on_gpu else "40x4")).strip()
DIARIZATION_INT8 = (os.getenv("DIARIZATION_INT8") or ("false" if _diar_on_gpu else "true")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DIARIZATION_THREADS = int((os.getenv("DIARIZATION_THREADS") or "4").strip())
# Longest a finished turn waits for the diariser to catch up before it is sent anyway.
DIARIZATION_MAX_WAIT_MS = int(
    (os.getenv("DIARIZATION_MAX_WAIT_MS") or ("1500" if _diar_on_gpu else "6000")).strip()
)
# Signs the one-time tickets that let the browser open the relay WebSocket. Set it when running
# more than one backend instance; otherwise a per-process random secret is fine.
STT_TICKET_SECRET = (os.getenv("STT_TICKET_SECRET") or os.urandom(32).hex()).strip()
