# Real-Time Conversation Intelligence

A real-time legal call assistant system that provides live AI-powered suggestions to operators during active calls with clients. Built with a FastAPI backend and Next.js frontend, featuring real-time speech-to-text transcription via AssemblyAI and task-specific LLM routing through LiteLLM over an OpenAI-compatible API surface.

App Screenshot

## ✨ Features

- **Real-Time Transcription**: Live speech-to-text using AssemblyAI WebSocket API (frontend connects directly for lowest latency)
- **Speaker Diarization**: AssemblyAI streaming speaker labels (`speaker_labels`) map two voices to **Staff** / **Customer** on a single laptop mic
- **AI-Powered Suggestions**: Intelligent, context-aware recommendations for operators
- **Legal Entity Integration**: Specialized for legal entity in singapore's legal assistance workflow
- **Live Conversation Intelligence**: Real-time analysis of ongoing conversations
- **Operator Support**: Actionable suggestions including follow-up questions, document requests, and issue identification
- **Customer Data Extraction**: Automatically extracts structured information (name, NRIC, address, purpose) from conversations
- **Customer History Lookup**: Uses extracted or manually corrected identity fields to retrieve prior case history from a read-only Postgres view



### Documentation

**[Data science, operations, and production readiness](docs/DATA_SCIENCE_AND_OPS.md)** — full runbook: architecture, env options, sync vs async, persistence, telemetry, and how to avoid “surprise drift” (versioning, eval, vendor drift). Shorter go-live list: [docs/PRODUCTION_CHECKLIST.md](docs/PRODUCTION_CHECKLIST.md).

## 🚀 Quick Start



### Prerequisites

- **Python 3.11+** (check with `python3 --version`)
- **Node.js 18+** and npm (check with `node --version` and `npm --version`)
- **AssemblyAI API Key** ([Get one here](https://www.assemblyai.com/))
- **LiteLLM proxy URL, API key, and model names**



### Installation & Setup



#### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/realtime-conversation-intelligence.git
cd realtime-conversation-intelligence
```



#### 2. Set Up Python Backend

```bash
# Create virtual environment
python3.11 -m venv realtime-venv

# Activate virtual environment
# On macOS/Linux:
# Create virtual environment
python3.11 -m venv realtime-venv

# Activate virtual environment
# On macOS/Linux:
source realtime-venv/bin/activate
# On Windows:
# realtime-venv\Scripts\activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
# On Windows:
# realtime-venv\Scripts\activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```



#### 3. Configure Environment Variables

Create a `.env` file in the project root directory:

```bash
# AssemblyAI for realtime transcription
ASSEMBLYAI_API_KEY=your_assemblyai_api_key_here

# LiteLLM / OpenAI-compatible proxy configuration for all LLM tasks
LLM_API_KEY=your_litellm_proxy_key_here
LLM_BASE_URL=http://your-litellm-host:4000
LLM_TIMEOUT_SECONDS=20
ROUTER_MODEL=gpt-4o-mini
SUGGESTION_MODEL=claude-sonnet-5
EXTRACTION_MODEL=claude-sonnet-5
SQL_LOOKUP_MODEL=claude-sonnet-5
SUGGESTION_TEMPERATURE=0.3
MAX_SUGGESTIONS=2

# API auth mode
REQUIRE_API_AUTH=false
API_AUTH_TOKEN=replace_with_long_random_value_when_enabled
AUTH_JWKS_URL=
AUTH_ISSUER=
AUTH_AUDIENCE=

# Restrict backend CORS origins (comma-separated)
BACKEND_CORS_ORIGINS=http://localhost:3000

# Usage guardrails
RATE_LIMIT_PER_MINUTE=30
DAILY_REQUEST_QUOTA=2000

# Session persistence: sqlite (default local) | dynamodb (AWS) | none
STORAGE_BACKEND=sqlite
SQLITE_DB_PATH=data/sessions.db
DYNAMODB_CONVERSATIONS_TABLE=
AWS_REGION=us-east-1

# Customer history lookup (read-only Postgres view)
CUSTOMER_HISTORY_DATABASE_URL=
CUSTOMER_HISTORY_VIEW=public.customer_history_view
CUSTOMER_HISTORY_QUERY_TIMEOUT_MS=2500
CUSTOMER_HISTORY_MAX_ROWS=10
```

**Note**: Replace the example AssemblyAI and LiteLLM values with your real credentials and model aliases. In this project, LiteLLM is the intended LLM runtime contract, so `LLM_BASE_URL` and `LLM_API_KEY` should both be set.  
You can also copy `.env.example` to `.env` and fill values.

#### 4. Configure Suggestion Settings (Optional)

Edit `config.json` to customize fallback model-routing defaults when env vars are not set:

```json
{
  "router_model": "gpt-4o-mini",
  "suggestion_model": "gpt-4o-mini",
  "extraction_model": "gpt-4o-mini",
  "sql_lookup_model": "gpt-4o-mini",
  "suggestion_temperature": 0.3,
  "max_suggestions": 2,
  "assemblyai_keyterms": []
}
```

Optional `assemblyai_keyterms`: array of strings passed to AssemblyAI streaming v3 as the `keyterms_prompt` query parameter (JSON-encoded). This replaces the older `word_boost` style usage on streaming; start empty and add only terms the model often mishears (max 100; see [AssemblyAI keyterms prompting](https://www.assemblyai.com/docs/streaming/keyterms-prompting)).

**Model routing**: task-specific env vars take precedence over `config.json`, which makes it easy to point the backend at LiteLLM aliases without editing code.

#### 5. Set Up Frontend

```bash
cd frontend
npm install
cd ..
```



### Running the Application



#### Start the Backend Server

In your terminal (with virtual environment activated):

```bash
uvicorn backend.api:app --host 0.0.0.0 --port 8000 --reload
```

The backend will be available at `http://localhost:8000`

**Available Endpoints**:

- `GET /health` – Liveness: process is up (include `APP_VERSION` / `GIT_SHA` when set).
- `GET /ready` – Readiness: dependency check; with `STRICT_READINESS=true`, returns 503 until LiteLLM and AssemblyAI configuration is present.
- `GET /metrics` – Prometheus text (when `METRICS_ENABLED=true`; optional `METRICS_TOKEN`).
- `GET /config` – Configuration introspection (requires auth when enabled)
- `GET /limits` – Active per-minute and daily quota values
- `POST /queue/suggestions` – Enqueue suggestion job (needs `ASYNC_JOBS_ENABLED=true` + worker when using async)
- `POST /queue/extract-customer-data` – Enqueue extraction job
- `GET /queue/jobs/{job_id}` – Poll job status and `result` payload
- `POST /sessions/` – Create a persisted call session id
- `GET /sessions/{session_id}` – Session metadata + recent stored events (same user only)
- `POST /suggest` – AI suggestions endpoint (accepts conversation transcript + optional session id)
- `POST /extract-customer-data` – Extract customer information (optional session id)
- `POST /customer-history` – Read-only customer-history lookup using name and/or NRIC / Work Permit ID (optional session id)

**Tests & ops**: `pip install -r requirements-dev.txt && pytest` · load probe: `python scripts/load_smoke.py` · production checklist: [docs/PRODUCTION_CHECKLIST.md](./docs/PRODUCTION_CHECKLIST.md).

#### Start the Frontend Development Server

Open a **new terminal** (keep backend running):

```bash
cd frontend
npm run dev
```

The frontend will be available at `http://localhost:3000`

#### Run with Docker Compose (Production-like Local)

```bash
docker compose up --build
```

**Async inference (production scale)**:

- Set `ASYNC_JOBS_ENABLED=true` and run the worker: `python -m backend.worker`.
- `INFERENCE_QUEUE_MODE=poll` uses SQLite locks and a **shared** DB file (good for local / single-node Docker).
- `INFERENCE_QUEUE_MODE=sqs` + `AWS_SQS_INFERENCE_QUEUE_URL` targets AWS SQS (recommended for multi-instance App Runner/ECS).
- Frontend: set `NEXT_PUBLIC_USE_ASYNC_JOBS=true`. In Docker, pass it as a **build-arg** (see `Dockerfile.frontend` + `docker-compose.yml`).



### Usage

1. **Open the Application**: Navigate to `http://localhost:3000` in your browser
2. **Start Transcription**: Click "Start session" (browser will request microphone access)
3. **Speak**: Staff and customer voices on the same laptop mic are labeled separately; use **Next voice is Staff/Customer** and **Swap roles** if needed
4. **View Suggestions**: AI-powered suggestions appear in real-time on the right side
5. **Use customer lookup**: After extraction fills name or NRIC / Work Permit ID, click **Obtain customer info** to search the read-only history view
6. **Stop**: Click "Stop" to end the transcription session

**How It Works**:

- Partial transcripts appear instantly as you speak, with Staff/Customer badges when diarization has locked roles
- When finalized, transcripts overwrite partial text (no duplicates)
- AI suggestions and customer extraction use role-labeled transcript context
- Suggestions update in real-time as the conversation progresses
- Customer history lookup prefers NRIC / Work Permit ID and falls back to exact customer-name matching



### Same-laptop diarization setup

This app uses **one microphone** (not WhatsApp/VoIP call bridging). Typical setup:

1. Staff opens the UI on their laptop and starts a session.
2. Customer speaks in-person or via speakerphone into the same room/mic.
3. AssemblyAI streaming diarization separates speakers; the first new voice defaults to **Staff** (change with “Next voice is Customer” before that speaker appears).
4. If early labels are swapped, click **Swap roles** — no need to restart the session.

**Persistence**:

- Default local storage uses SQLite (`data/sessions.db`).
- Production on AWS uses DynamoDB (`STORAGE_BACKEND=dynamodb`; see `infra/aws/README.md`).

**Auth behavior**:

- If Cognito frontend vars are configured, users can login via Hosted UI.
- Backend validates JWTs when `REQUIRE_API_AUTH=true` and JWT settings are configured (`AUTH_JWKS_URL`, `AUTH_ISSUER`, `AUTH_AUDIENCE`).



## 🏗️ Architecture



### How It Works

1. **Real-Time Transcription (Frontend)**:
  - Frontend streams laptop-mic audio directly to AssemblyAI over WebSocket
  - Streaming diarization (`speaker_labels=true`, `max_speakers=2`) labels speakers A/B
  - UI maps labels to **Staff** / **Customer** (lock next voice + Swap roles if inverted)
  - Partial text renders immediately; finals overwrite partials to avoid duplicates
  - Suggest/extract receive role-labeled context (`Staff: …` / `Customer: …`)
2. **Transcript Analysis (Backend)**:
  - Finalized labeled transcript turns are posted to `/suggest` endpoint
  - Backend processes conversation context through LiteLLM-routed models
3. **AI Suggestions (Two-Agent Pipeline)**:
  - **Router Agent**: Analyzes conversation and decides when suggestions are needed
  - **Suggestion Agent**: Generates actionable recommendations including:
    - Follow-up questions to gather essential information
    - Legal issue identification
    - Document requests
    - Urgency assessment
    - Natural language responses for operators
4. **Customer Data Extraction**:
  - `/extract-customer-data` endpoint extracts structured information (name, NRIC, address, purpose) from **Customer**-attributed lines
5. **Customer History Lookup**:
  - `/customer-history` uses customer identity fields to perform a read-only lookup against a curated Postgres customer-history view and returns case summaries for the operator



## 📁 Project Structure

```
realtime-conversation-intelligence/
├── backend/                    # FastAPI backend
│   ├── api.py                 # FastAPI application and routes
│   ├── suggestions.py         # Two-agent AI suggestion endpoint
│   ├── router_agent.py        # Router agent logic
│   ├── suggestion_agent.py    # Suggestion agent logic
│   ├── customer_data_extractor.py  # Customer data extraction
│   ├── prompt_loader.py       # Prompt loading utility
│   ├── config.py              # Environment and configuration
│   ├── session_store.py       # SQLite + DynamoDB session persistence
│   └── prompts/               # Editable prompt files
│       ├── router_system_prompt.txt
│       ├── router_user_prompt.txt
│       ├── suggestion_system_prompt.txt
│       ├── suggestion_user_prompt.txt
│       └── fallback_suggestions.json
├── frontend/                  # Next.js frontend
│   ├── app/
│   │   ├── page.tsx           # Main conversation UI
│   │   └── layout.tsx          # Next.js layout
│   └── package.json
├── config.json                # Model defaults
├── requirements.txt           # Python dependencies
└── README.md                  # This file
```



## 🎨 Customization



### Customizing AI Prompts

All AI prompts are stored in separate files for easy customization. Edit the files in `backend/prompts/` to modify agent behavior:

**Router Agent Prompts** (controls when suggestions are generated):

- `router_system_prompt.txt` – System instructions for the router agent
- `router_user_prompt.txt` – User prompt template (uses `{conversation_transcript}` placeholder)

**Suggestion Agent Prompts** (controls what suggestions are generated):

- `suggestion_system_prompt.txt` – System instructions for the suggestion agent
- `suggestion_user_prompt.txt` – User prompt template (uses `{conversation_transcript}` and `{max_suggestions}` placeholders)

**Fallback Suggestions** (shown when the AI fails):

- `fallback_suggestions.json` – JSON array of fallback suggestion objects

**Note**: Prompt files support template placeholders (e.g., `{conversation_transcript}`) which are automatically replaced at runtime. Do not modify these placeholders unless you understand the code structure.

Changes take effect after restarting the backend server.

## 🐛 Troubleshooting



### Common Issues

**Microphone not working**:

- Check browser permissions (allow microphone access when prompted)
- Ensure `ASSEMBLYAI_API_KEY` is configured in backend `.env`
- Try refreshing the page and granting permissions again

**Suggestions not appearing**:

- Verify `LLM_API_KEY`, `LLM_BASE_URL`, and task model names are configured in your `.env` file
- Check that your LiteLLM proxy is reachable and healthy
- Review backend logs for error messages
- Test the `/config` endpoint: `curl http://localhost:8000/config`

**Customer history lookup not working**:

- Ensure `CUSTOMER_HISTORY_DATABASE_URL` is configured
- Verify `CUSTOMER_HISTORY_VIEW` points at a readable view with the expected columns
- Confirm the backend database role is read-only and has access to the configured view

**Backend connection issues**:

- Ensure backend is running on port 8000
- Check that virtual environment is activated
- Verify all dependencies are installed: `pip list`
- Review FastAPI logs for detailed error messages

**Frontend connection issues**:

- Ensure frontend is running on port 3000
- Check that backend is accessible at `http://localhost:8000`
- Verify CORS settings if accessing from different origin
- Ensure AssemblyAI WebSocket can connect (corporate networks may require allowing `wss://streaming.assemblyai.com`)

**Duplicate lines in conversation**:

- The UI normalizes final vs partial transcripts automatically
- If duplicates persist, refresh the page and try again
- Check browser console for errors



### Debugging

**Backend Logging**: The suggestions endpoint provides detailed logging:

- Input conversation transcripts with character counts
- API call details (model, request parameters)
- Output suggestions with type, text, confidence, priority
- Error traces and fallback suggestions

**Check Backend Health**:

```bash
curl http://localhost:8000/health
```

**Check Configuration**:

```bash
curl http://localhost:8000/config
```



## 📝 Notes

- CORS is now controlled by `BACKEND_CORS_ORIGINS`. Use explicit production domains only.
- `REQUIRE_API_AUTH=true` enforces bearer token checks for backend endpoints as a production hardening scaffold (enable once real JWT login is integrated).
- JWT auth mode is enabled automatically when `AUTH_JWKS_URL`, `AUTH_ISSUER`, and `AUTH_AUDIENCE` are configured. The backend validates bearer JWTs with JWKS.
- Rate limiting and quotas are enabled per authenticated user key (`RATE_LIMIT_PER_MINUTE`, `DAILY_REQUEST_QUOTA`).
- The backend issues short-lived AssemblyAI tokens via `/assemblyai-token`; your permanent AssemblyAI key stays server-side.
- The backend provides comprehensive logging for all suggestion requests, making it easy to debug and monitor the system.
- Suggestions are generated in real-time from finalized transcript turns and update automatically.
- The system is optimized for legal entity in singapore's workflow, providing context-aware recommendations for legal assistance operators.



## ☁️ AWS Deployment

AWS deployment manifests and instructions are in:

- `infra/aws/README.md`
- `infra/aws/backend.apprunner.yaml`
- `infra/aws/frontend.apprunner.yaml`



## 📄 License

See [LICENSE](./LICENSE) file for details.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📧 Support

For issues and questions, please open an issue on GitHub.
