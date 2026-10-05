---
name: run-app
description: Start this app locally (demo customer DB, backend, frontend) and smoke-test it. Use when asked to run, start or check the app, or to confirm a change works end to end.
---

# Run the app locally

All commands from the repo root. The venv is `.venv`.

## 1. Customer DB

```bash
.venv/bin/python scripts/demo_db.py
```

Idempotent. Its Postgres does not survive a WSL restart, so run it again after a reboot. It prints
`CUSTOMER_HISTORY_DATABASE_URL`; `.env` should already contain it.

## 2. Backend

```bash
nohup .venv/bin/uvicorn backend.api:app --host 127.0.0.1 --port 8000 > /tmp/rci-backend.log 2>&1 &
curl -s localhost:8000/ready
```

- Check `/ready`: `llm_configured`, `transcription_configured` and `customer_db_configured` should be true.
  Nemotron takes a few seconds to load; then `diarization.ready` is true.
- On this laptop Nemotron runs on CPU (`40x4` mode) and lags in live calls. For real use, run the backend
  on the GPU server instead (the `gpu-server` skill).
- If something else already listens on :8000 (an old backend), check `ss -ltnp | grep 8000` and stop that
  process first.

## 3. Frontend

```bash
cd frontend && nohup npm run dev > /tmp/rci-frontend.log 2>&1 &
```

Open http://localhost:3000 and click **Start session**. If `:3000` is taken by an old `next start`, stop it:
a stale server serves an old build.

## Smoke tests (no microphone needed)

Customer lookup:

```bash
curl -s -X POST localhost:8000/customer-history -H 'Content-Type: application/json' \
  -d '{"nric_worker_permit_id":"S1234567A"}'
```

Expect `status: ok` and Katherine Liao with 2 cases.

Both agents for one turn (streams NDJSON):

```bash
curl -sN -X POST localhost:8000/assist -H 'Content-Type: application/json' -d '{"turns":[
  {"role":"staff","text":"Hello, how can I help?"},
  {"role":"customer","text":"Hi, my name is Katherine Liao, my IC is S one two three four five six seven A. Brightpath cut my leave again."}]}'
```

Expect this event order: `customer` (heard) → `history` loading → `history` ok → `customer` (records) →
`suggesting` → `suggestions` citing `CASE-2026-03117` → `customer` (ai) → `done`.

## Browser end-to-end with a fake microphone

Headless Chromium can play a WAV file as the microphone:

```text
--use-fake-ui-for-media-stream --use-fake-device-for-media-stream --use-file-for-fake-audio-capture=<call.wav>%noloop
```

- Make a two-voice WAV with the LLM gateway's `openai.tts-1` model (voices `onyx` for staff, `nova` for the
  customer, 1 s gaps).
- Drive the page with Playwright: click "Start session", wait for the audio to finish, then read
  `.turn-card`, `#cust-name`, `#cust-id`, `.hero-card__topic` and `.case-item__id`.
