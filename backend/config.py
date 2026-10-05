"""Settings, read once from the environment (.env at the repo root). See .env.example."""

import os
import pathlib

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=True)

REPO_ROOT = pathlib.Path(__file__).parent.parent


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _int(name: str, default: int) -> int:
    return int(_str(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(_str(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return _str(name, "true" if default else "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in _str(name, default).split(",") if item.strip()]


# --- Transcription: AssemblyAI streaming (the words) ------------------------------------------

ASSEMBLYAI_API_KEY = _str("ASSEMBLYAI_API_KEY")
# u3-rt-pro: the standard model mishears spoken digit strings (it dropped digits from IDs), breaking the lookup.
ASSEMBLYAI_SPEECH_MODEL = _str("ASSEMBLYAI_SPEECH_MODEL", "u3-rt-pro")
# Extra terms to listen for, sent as `keyterms_prompt` (max 100).
ASSEMBLYAI_KEYTERMS = _list("ASSEMBLYAI_KEYTERMS")[:100]
# Turn detection. Shorter end-of-turn silence makes finished lines appear sooner (~1.0 s vs ~1.6 s
# with u3-rt-pro) without hurting accuracy.
ASSEMBLYAI_STREAM_PARAMS = {
    "min_end_of_turn_silence_when_confident": str(
        _int("ASSEMBLYAI_END_OF_TURN_SILENCE_MS", 240)
    ),
    "max_turn_silence": str(_int("ASSEMBLYAI_MAX_TURN_SILENCE_MS", 1000)),
}

# --- Diarisation: NVIDIA Nemotron 3 Diarization (who said each word) --------------------------
# Needs the `diarization` extra. If the model fails to load, turns arrive without speakers and
# staff assign roles in the UI.

_default_diar_model = REPO_ROOT / "data" / "models" / "Nemotron-3-Diarization"
DIARIZATION_MODEL = _str(
    "DIARIZATION_MODEL",
    (
        str(_default_diar_model)
        if _default_diar_model.exists()
        else "nvidia/Nemotron-3-Diarization"
    ),
)
DIARIZATION_DEVICE = _str("DIARIZATION_DEVICE", "cpu").lower()
_diar_on_gpu = DIARIZATION_DEVICE.startswith("cuda")
# Streaming chunk: low_latency (1.04 s), very_low_latency (0.64 s), ultra_low_latency (0.32 s), or
# "<chunk>x<right_context>" in 80 ms frames. A laptop CPU cannot keep up with 1.04 s; 40x4 (3.5 s) can.
DIARIZATION_MODE = _str("DIARIZATION_MODE", "low_latency" if _diar_on_gpu else "40x4")
DIARIZATION_INT8 = _bool("DIARIZATION_INT8", not _diar_on_gpu)
DIARIZATION_THREADS = _int("DIARIZATION_THREADS", 4)
# Longest a finished turn waits for the diariser to catch up before it is sent anyway.
DIARIZATION_MAX_WAIT_MS = _int(
    "DIARIZATION_MAX_WAIT_MS", 1500 if _diar_on_gpu else 6000
)
# Signs the one-time tickets that let the browser open the relay WebSocket. Set it when running
# more than one backend instance; otherwise a per-process random secret is fine.
STT_TICKET_SECRET = _str("STT_TICKET_SECRET") or os.urandom(32).hex()

# --- LLM: LiteLLM / OpenAI-compatible proxy for both agents -----------------------------------

LLM_API_KEY = _str("LLM_API_KEY")
LLM_BASE_URL = _str("LLM_BASE_URL")
LLM_TIMEOUT_SECONDS = _float("LLM_TIMEOUT_SECONDS", 20)
LLM_MAX_RETRIES = _int("LLM_MAX_RETRIES", 2)
# Optional reasoning effort for GPT-5-family models (none|minimal|low). Empty = provider default.
LLM_REASONING_EFFORT = _str("LLM_REASONING_EFFORT")

SUGGESTION_MODEL = _str("SUGGESTION_MODEL", "gpt-4o-mini")
SUGGESTION_TEMPERATURE = _float("SUGGESTION_TEMPERATURE", 0.3)
SUGGESTION_MAX = _int("MAX_SUGGESTIONS", 1)
SUGGESTION_TIMEOUT_SECONDS = _float("SUGGESTION_TIMEOUT_SECONDS", LLM_TIMEOUT_SECONDS)
SUGGESTION_MAX_TOKENS = _int("SUGGESTION_MAX_TOKENS", 900)

EXTRACTION_MODEL = _str("EXTRACTION_MODEL", SUGGESTION_MODEL)
EXTRACTION_TIMEOUT_SECONDS = _float("EXTRACTION_TIMEOUT_SECONDS", LLM_TIMEOUT_SECONDS)

# --- Customer DB: read-only Postgres view ------------------------------------------------------

CUSTOMER_HISTORY_DATABASE_URL = _str("CUSTOMER_HISTORY_DATABASE_URL")
CUSTOMER_HISTORY_VIEW = _str("CUSTOMER_HISTORY_VIEW", "public.customer_history_view")
CUSTOMER_HISTORY_QUERY_TIMEOUT_MS = _int("CUSTOMER_HISTORY_QUERY_TIMEOUT_MS", 2500)
CUSTOMER_HISTORY_MAX_ROWS = _int("CUSTOMER_HISTORY_MAX_ROWS", 10)
# Optional extra view columns returned with the customer (the demo view has none).
CUSTOMER_HISTORY_EXTRA_COLUMNS = _list("CUSTOMER_HISTORY_EXTRA_COLUMNS")

# --- Server -------------------------------------------------------------------------------------

BACKEND_CORS_ORIGINS = _list("BACKEND_CORS_ORIGINS", "http://localhost:3000")
