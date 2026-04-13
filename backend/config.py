import os
import pathlib
import json
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True), override=True)

ASSEMBLYAI_API_KEY = (os.getenv("ASSEMBLYAI_API_KEY") or "").strip()
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()

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

# Streaming v3 uses query param `keyterms_prompt` (JSON array string), not legacy `word_boost`.
_raw_keyterms = CONFIG.get("assemblyai_keyterms") or []
if not isinstance(_raw_keyterms, list):
    _raw_keyterms = []
ASSEMBLYAI_KEYTERMS = [str(x).strip() for x in _raw_keyterms if str(x).strip()][:100]
