# CLAUDE.md

Live call assistant for a Singapore legal-advice centre. Four parts:

1. **Transcription + diarisation**: browser mic → `WS /ws/stt` relay → AssemblyAI (words) + Nemotron 3 Diarization (speaker per word).
2. **Entity agent**: instant regex + LLM extraction of name / contact number / email / purpose → customer DB lookup (phone or email match = verified; NRIC and address are deliberately not collected).
3. **Suggestion agent** (principal agent): transcript + customer record → what Staff should say next, citing case IDs.
4. **Orchestration**: `POST /assist` runs 2 and 3 for each customer turn and streams NDJSON events to the UI.
5. **Wrap-up**: End call → `POST /wrapup` (case note, actions, follow-up, message to caller) → Save → `POST /cases` writes the case to the demo DB, so the next call picks it up.

README.md has the step-by-step demo start on the GPU server, a short architecture overview and the endpoints.

## Commands

```bash
source .venv/bin/activate                 # one venv at the repo root; deps in pyproject.toml
pip install -e ".[dev,diarization]"       # add --extra-index-url https://download.pytorch.org/whl/cpu on CPU
python scripts/demo_db.py                 # embedded Postgres with demo customers; re-run after a reboot
uvicorn backend.api:app --port 8000       # backend (loads Nemotron at startup)
cd frontend && npm run dev                # frontend on :3000

pytest                                    # backend tests
ruff check . && ruff format .             # backend lint + format
cd frontend && npm test                   # frontend tests (node --test, app/lib/*.test.ts)
cd frontend && npm run format && npm run build
```

## Where things live

- `backend/orchestrator.py`: the order agents run in for a turn, and the `/assist` event schema (documented at the top).
- `backend/agents/`: `entity_agent.py`, `suggestion_agent.py`. Each calls the LLM once through `backend/llm.py`.
- `backend/prompts/<agent>/`: `system.md` (sent as is) and `user.md` (a `str.format` template). Edit prompts here, never inline in Python.
- `backend/profile.py`: field precedence (manual > records > heard > ai) and when to look a caller up. `frontend/app/lib/customer-profile.ts` mirrors the precedence; both are tested against `tests/fixtures/profile_precedence.json`, so change all three together.
- `backend/customer_history.py`: read-only Postgres lookup and how the record is rendered into the suggestion prompt.
- `backend/issue_guides.py`: the service guide (advice per issue type) from the DB's `issue_guides`, given to both agents. Demo content: real SG channels in general terms.
- `backend/wrapup.py` + `agents/wrapup_agent.py` + `case_store.py`: end of call. `case_store` is the only write path, demo tables only, behind `CASE_STORE_ENABLED`.
- `scripts/simulate_call.py`: an LLM caller runs whole calls through a running backend and checks each stage. Use it after any prompt change; prompts are cached, so restart the backend first.
- `backend/stt_relay.py` + `backend/diarization/`: the relay and Nemotron.
- `backend/config.py`: every setting, with defaults; `.env.example` lists them.
- `frontend/app/hooks/`: `useLiveTranscript` (relay, mic, speakers), `useAssist` (one `/assist` stream per customer turn; started early on the relay's provisional speakers in `PendingTurn`, and kept only if the final turns match exactly). Logic worth testing goes in `frontend/app/lib/` as pure functions with a `*.test.ts` beside it.

## Conventions

- Agent order and data flow are decided in the backend (`orchestrator.py`); the frontend only renders events and sends staff actions.
- New LLM calls go through `get_async_llm_client()` in `backend/llm.py` (LiteLLM proxy, async only).
- No demo or scripted paths: the app is live-mic only. `scripts/demo_db.py` is the only demo data.
- Backend tests use fakes for the LLM, DB and agents (see `tests/test_orchestrator.py`); they never call external services.
- Python is formatted with ruff (110 columns), the frontend with prettier (120 columns). CI checks both.
- Next.js here is 16.x and may differ from what you know: check `frontend/node_modules/next/dist/docs/` before using a Next API.

## Gotchas

- **Nemotron needs a GPU for live use.** The laptop CPU is too slow and erratic; run the backend on the AWS GPU server with `scripts/gpu.sh` (start / deploy / connect / stop; it costs about US$1/hour while running and stops itself when idle).
- **Hugging Face is blocked on the PwC network.** Nemotron weights load from `data/models/Nemotron-3-Diarization`.
- **The CA bundle (`SSL_CERT_FILE` on /mnt/c) takes seconds to load.** Reuse `assemblyai.ssl_context()`; never build an SSL context per request.
- **AssemblyAI speech model is `u3-rt-pro`.** The standard model drops digits from spoken numbers, which breaks the phone lookup.
- `data/` (model weights, demo DB) and `.env` are not in git.
