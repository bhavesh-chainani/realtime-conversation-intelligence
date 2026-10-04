# Real-Time Conversation Intelligence

Live assistant for staff on legal-assistance calls. It transcribes the call, works out who is speaking,
recognises the caller from their NRIC / FIN, pulls their prior cases from the customer database, and suggests
what staff should say next, citing those cases.

## How it works

```text
Browser mic ──PCM──▶ WS /ws/stt (backend relay) ──▶ AssemblyAI streaming        → the words
                                                └─▶ Nemotron 3 Diarization     → who said each word
           ◀── finished turns, split wherever the speaker changes (A / B → Staff / Customer)

On each customer turn:
  1. Entity / DB agent   instant NRIC + name regex (browser) and POST /extract-customer-data (LLM)
                         → POST /customer-history: read-only Postgres view, NRIC match first, then name
  2. Principal agent     POST /suggest with the transcript + the customer record
                         → one suggestion; case IDs it cites must exist in the record
```

- **Diarisation.** The relay sends the same audio to AssemblyAI (words and timestamps) and to
  [Nemotron 3 Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization) (speaker probabilities
  every 10 ms). Each word gets the speaker active during it (`backend/diarization/merge.py`).
  - A finished turn shows as *identifying speaker* until the diariser has covered its last word, or until
    `DIARIZATION_MAX_WAIT_MS` passes.
  - Without a loaded model, turns arrive unlabelled and staff assign roles in the UI (Next voice, Swap
    roles, or click a turn).
- **Identity.** Only an NRIC match counts as verified. A name-only match is shown as a *possible match*, and
  the agent is told not to discuss those cases until the NRIC is confirmed.
- **Orchestration.** The order of the two agents currently lives in the browser (`handleCustomerTurn` in
  `frontend/app/page.tsx`).

## Repository layout

```text
backend/
  api.py                      FastAPI app: /health, /ready, /stt/session, routers
  stt_relay.py                WS /ws/stt: AssemblyAI words + Nemotron speakers
  assemblyai.py               AssemblyAI streaming connection
  diarization/                nemotron.py (model, per-call sessions), merge.py (word → speaker)
  suggestion_agent.py         principal agent
  suggestions.py              POST /suggest (agent + static fallback)
  customer_data_extractor.py  entity agent: LLM extraction of name / NRIC / address / purpose
  quick_entities.py           regex NRIC / name extraction (mirrors frontend/app/lib/quick-entities.ts)
  customer_history.py         POST /customer-history + customer-record formatting for the prompt
  prompts/                    suggestion prompts and fallback suggestions (editable without code changes)
  llm.py, config.py, text_guard.py, prompt_loader.py
frontend/app/                 Next.js UI: page.tsx, components/, lib/
scripts/
  demo_db.py                  embedded Postgres with demo customers and cases
  setup_gpu_host.sh, install_gpu_services.sh, gpu_connect.sh, gpu_host.conf   GPU host for Nemotron
tests/                        pytest suite
```

## Setup

Requirements:

- Python 3.11+ and Node.js 22+ (the frontend tests use Node's built-in TypeScript stripping)
- An AssemblyAI API key
- A LiteLLM (OpenAI-compatible) proxy URL and key

```bash
python3 -m venv .venv && source .venv/bin/activate
# dev = tests, lint, demo DB; diarization = Nemotron (torch, transformers). CPU-only machine: smaller torch wheels
pip install -e ".[dev,diarization]" --extra-index-url https://download.pytorch.org/whl/cpu
cp .env.example .env                          # set ASSEMBLYAI_API_KEY, LLM_API_KEY, LLM_BASE_URL
(cd frontend && npm install)
```

**Customer database.** `python scripts/demo_db.py` starts an embedded Postgres in `data/demo_pg` and seeds the
demo customers. For example, Katherine Liao, S1234567A, has open and closed cases. The script prints the
`CUSTOMER_HISTORY_DATABASE_URL` and `CUSTOMER_HISTORY_EXTRA_COLUMNS` values to put in `.env`. Re-run it after a
reboot.

## Run

```bash
uvicorn backend.api:app --host 127.0.0.1 --port 8000    # loads Nemotron at startup
cd frontend && npm run dev                              # http://localhost:3000
```

Open the app, click **Start session**, and speak with two voices into the mic:

- The first new voice is Staff by default. Change it with **Next voice**, or fix it later with **Swap roles**.
- Say an NRIC ("my IC is S1234567A") and the caller card fills from the database.
- The next suggestion cites the caller's cases.

**Endpoints:**

| Endpoint | What it does |
| --- | --- |
| `GET /health` | Liveness |
| `GET /ready` | Whether the LLM and transcription are configured, the diariser is loaded, and the customer DB is set |
| `GET /stt/session` | One-time ticket for the relay |
| `WS /ws/stt` | The relay |
| `POST /suggest` | Principal agent |
| `POST /extract-customer-data` | LLM entity extraction |
| `POST /customer-history` | DB lookup by NRIC and/or name |

## Configuration

- **`.env`** holds every setting; defaults and comments live in `backend/config.py`. AssemblyAI uses
  `u3-rt-pro` by default because the standard model mishears spoken NRICs.
- **`backend/prompts/`**: the principal agent's system and user prompts, and the fallback suggestions.

## GPU host (Nemotron)

**Run Nemotron on an NVIDIA GPU.** There, the 1.04 s streaming profile adds well under a second per turn. A
laptop CPU cannot keep up: `DIARIZATION_DEVICE=cpu` defaults to int8 and 3.5 s chunks, and turns then wait
several seconds for their speakers.

```bash
# On a Linux GPU machine (RTX 30-series or newer, or a cloud L4 / A10):
scripts/setup_gpu_host.sh               # venv, CUDA torch, model weights into data/models/
sudo scripts/install_gpu_services.sh    # demo DB + backend as systemd services, idle and nightly auto-stop
# On the laptop: tunnel the backend to localhost:8000, then run the frontend as usual
scripts/gpu_connect.sh <gpu-host-ip>
```

The server stops itself to save cost:

- after `IDLE_MINUTES` with no app use;
- at `NIGHTLY_STOP`.

Both are set in `scripts/gpu_host.conf`. A stopped server keeps its disk; start it again from the AWS console.
`gpu_connect.sh` shows when the next stop is due.

Hugging Face downloads may be blocked on corporate networks. If so, fetch the weights elsewhere and set
`DIARIZATION_MODEL` to the local folder.

## Tests

```bash
pytest                    # backend
cd frontend && npm test   # frontend lib tests (node --test)
```

## License

MIT, see [LICENSE](LICENSE).
