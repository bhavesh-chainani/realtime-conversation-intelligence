# Real-Time Conversation Intelligence

Live assistant for staff on legal-assistance calls. It transcribes the call, works out who is speaking,
recognises the caller from their phone number or email, pulls their prior cases from the customer database, and
suggests what staff should say next, citing those cases.

## How it works

```text
Browser mic ──PCM──▶ WS /ws/stt (backend relay) ──▶ AssemblyAI streaming        → the words
                                                └─▶ Nemotron 3 Diarization     → who said each word
           ◀── finished turns, split wherever the speaker changes (A / B → Staff / Customer)

Each customer turn ──▶ POST /assist (NDJSON stream, backend/orchestrator.py)
  1. instant regex on the turn (phone, email, "my name is …") ──▶ customer DB lookup → caller card, prior cases
  2. after a 250 ms debounce, in parallel:
       entity agent      LLM extraction of name / phone / email / purpose (while fields are missing)
       suggestion agent  transcript + customer record ──▶ what Staff should say next, citing case IDs
  3. if extraction reveals a new identity with a different record, the suggestion is redone with it
```

- **Diarisation.** The relay sends the same audio to AssemblyAI (words and timestamps) and to
  [Nemotron 3 Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization) (speaker probabilities
  every 10 ms). Each word gets the speaker active during it (`backend/diarization/merge.py`).
  - A finished turn shows as _identifying speaker_ until the diariser has covered it, or until
    `DIARIZATION_MAX_WAIT_MS` passes.
  - Without a loaded model, turns arrive unlabelled and staff assign roles in the UI (Next voice, Swap
    roles, or click a turn).
- **Identity.** A phone or email match counts as verified and fills the caller card from the record. A name-only
  match shows as a _possible match_, and the suggestion agent asks for a phone number or email before discussing
  cases. NRICs and addresses are deliberately not collected.
  - Phones are Singapore numbers compared on their 8 digits (`+65 9123 4567` = `91234567`); emails are compared
    case-insensitively. The lookup tries phone, then email, then name.
- **Field precedence.** Staff edits always win, then DB records, then what was heard (regex), then LLM
  extraction. The rule lives in `backend/profile.py` and `frontend/app/lib/customer-profile.ts`; both test
  suites check it against `tests/fixtures/profile_precedence.json`.
- **Cancellation.** The browser aborts the previous `/assist` request when the next turn arrives, which
  cancels its LLM calls on the server.

## Repository layout

```text
backend/
  api.py                  FastAPI app: /health, /ready, /stt/session, routers
  orchestrator.py         POST /assist: runs both agents for a turn, streams NDJSON events
  agents/
    entity_agent.py       LLM extraction of caller details
    suggestion_agent.py   what Staff should say next (the principal agent), with a static fallback
  prompts/<agent>/        system.md, user.md (str.format template); suggestion/fallback.json
  profile.py              field precedence, when to look up, records prefill
  quick_entities.py       instant regex for spoken / written phones and emails, and self-introduced names
  customer_history.py     read-only Postgres lookup; POST /customer-history (manual Look up)
  stt_relay.py            WS /ws/stt: AssemblyAI words + Nemotron speakers
  assemblyai.py           AssemblyAI streaming connection
  diarization/            nemotron.py (model, per-call sessions), merge.py (word → speaker)
  llm.py, config.py, prompt_loader.py, text_guard.py
frontend/app/
  page.tsx                composes the hooks into the workspace
  hooks/                  useLiveTranscript (relay, mic, speakers), useAssist (/assist stream), useCallClock
  lib/                    pure, unit-tested logic: assist stream + reducer, precedence, transcript, relay
  components/             header, transcript, suggestion, caller card
scripts/
  demo_db.py              embedded Postgres with demo customers and cases
  gpu.sh                  start / deploy / connect / stop the AWS GPU server
  setup_gpu_host.sh, install_gpu_services.sh, gpu_host.conf
tests/                    pytest suite (frontend tests live next to the code as *.test.ts)
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

**Nemotron weights** load from `data/models/Nemotron-3-Diarization` when present, otherwise from Hugging Face.
`scripts/setup_gpu_host.sh` downloads them.

## Run

```bash
uvicorn backend.api:app --host 127.0.0.1 --port 8000    # loads Nemotron at startup
cd frontend && npm run dev                              # http://localhost:3000
```

Open the app, click **Start session**, and speak with two voices into the mic:

- The first new voice is Staff by default. Change it with **Next voice**, or fix it later with **Swap roles**.
- Say a phone number ("my number is nine one two three, four five six seven") or an email
  ("katherine dot liao at example dot com") and the caller card fills from the database.
- The next suggestion cites the caller's cases.

A laptop CPU runs Nemotron too slowly for live calls (turns wait seconds for speakers); use the GPU host below.

**Endpoints:**

| Endpoint                 | What it does                                                       |
| ------------------------ | ------------------------------------------------------------------ |
| `GET /health`            | Liveness                                                           |
| `GET /ready`             | LLM and transcription configured, diariser loaded, customer DB set |
| `GET /stt/session`       | One-time ticket for the relay                                      |
| `WS /ws/stt`             | The relay                                                          |
| `POST /assist`           | Both agents for one customer turn, as an NDJSON event stream       |
| `POST /customer-history` | Manual lookup by contact number, email and/or name                 |

`/assist` events, one JSON object per line: `customer` (a patch to the caller card, with its source),
`history` (`loading`, then the lookup result), `suggesting`, `suggestions`, `error` (non-fatal, per stage)
and `done`. The full schema is documented at the top of `backend/orchestrator.py`.

## Configuration

- **`.env`** holds every setting; defaults and comments live in `backend/config.py`. AssemblyAI uses
  `u3-rt-pro` by default because the standard model drops digits from spoken numbers.
- **Customer DB view** (`CUSTOMER_HISTORY_VIEW`, read-only): one row per case with `customer_name`,
  `contact_number`, `email`, `case_id`, `company`, `case_type`, `case_status`, `case_summary`. Further columns
  listed in `CUSTOMER_HISTORY_EXTRA_COLUMNS` are returned with the customer.
- **`backend/prompts/<agent>/`**: each agent's system prompt and user template, editable without code
  changes. Only `user.md` is passed through `str.format`.

## GPU host (Nemotron)

**Run Nemotron on an NVIDIA GPU.** There, the 1.04 s streaming profile adds well under a second per turn. A
laptop CPU cannot keep up: `DIARIZATION_DEVICE=cpu` defaults to int8 and 3.5 s chunks, and turns then wait
several seconds for their speakers.

The project's server is an AWS EC2 instance named `rci-gpu`: a g4dn.xlarge (NVIDIA T4) in ap-southeast-2b, which keeps
up with the 1.04 s profile. Manage it from the
laptop with the AWS CLI:

```bash
scripts/gpu.sh status     # state, IP, and when it will stop itself
scripts/gpu.sh start      # start it and wait for SSH (the public IP changes on every start)
scripts/gpu.sh deploy     # copy this checkout, install, restart the services, wait for /ready
scripts/gpu.sh connect    # tunnel its backend to localhost:8000; then: cd frontend && npm run dev
scripts/gpu.sh stop       # stop it (the disk and setup are kept)
```

It stops itself after `IDLE_MINUTES` without app use and at `NIGHTLY_STOP`, both set in
`scripts/gpu_host.conf`. The server keeps its own `.env`, model weights and demo DB; `deploy` never
overwrites them.

**A new GPU machine:**

1. Copy the repo to it.
2. Run `scripts/setup_gpu_host.sh` (venv, CUDA torch, model weights).
3. Add a `.env` with `DIARIZATION_DEVICE=cuda`.
4. Run `sudo scripts/install_gpu_services.sh` (systemd services and auto-stop).

Hugging Face downloads may be blocked on corporate networks. If so, fetch the weights elsewhere and set
`DIARIZATION_MODEL` to the local folder.

## Development

```bash
pytest                                    # backend tests
ruff check . && ruff format --check .     # backend lint / format
cd frontend && npm test                   # frontend tests (node --test)
npm run format:check && npm run build     # frontend format / build
```

CI runs all of these on every pull request.

## License

MIT, see [LICENSE](LICENSE).
