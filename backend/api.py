from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .suggestions import router as suggest_router
from .customer_data_extractor import router as customer_data_router
import logging

# Configure logging for the entire application
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(suggest_router)
app.include_router(customer_data_router)

@app.get('/health')
async def health():
    return {"ok": True}

@app.get('/config')
async def config():
    from .config import (
        ASSEMBLYAI_API_KEY,
        OPENAI_API_KEY,
        SUGGESTION_MODEL,
        ASSEMBLYAI_KEYTERMS,
    )
    return {
        "assemblyai_key_loaded": bool(ASSEMBLYAI_API_KEY),
        "openai_api_key_loaded": bool(OPENAI_API_KEY),
        "suggestion_model": SUGGESTION_MODEL,
        "assemblyai_keyterms_count": len(ASSEMBLYAI_KEYTERMS),
    }

@app.get('/assemblyai-key')
async def get_assemblyai_key():
    """Returns the AssemblyAI API key and optional streaming keyterms (keyterms_prompt source)."""
    from .config import ASSEMBLYAI_API_KEY, ASSEMBLYAI_KEYTERMS
    if not ASSEMBLYAI_API_KEY:
        return {"error": "AssemblyAI API key not configured in environment variables"}
    out = {"api_key": ASSEMBLYAI_API_KEY}
    if ASSEMBLYAI_KEYTERMS:
        out["keyterms_prompt"] = ASSEMBLYAI_KEYTERMS
    return out

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.api:app", host="0.0.0.0", port=8000, reload=True)