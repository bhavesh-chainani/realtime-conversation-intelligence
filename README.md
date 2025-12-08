## Real-Time Legal Assistant – Pro Bono SG

A real-time legal call assistant system that provides live AI-powered suggestions to operators during active calls with clients. Built with FastAPI backend and Next.js frontend, featuring real-time speech-to-text transcription via AssemblyAI and intelligent suggestions powered by OpenAI.

### Features

- **Real-Time Transcription**: Live speech-to-text using AssemblyAI WebSocket API (frontend connects directly for lowest latency)
- **AI-Powered Suggestions**: Intelligent, context-aware recommendations for operators
- **Pro Bono SG Integration**: Specialized for Pro Bono SG's legal assistance workflow
- **Live Conversation Intelligence**: Real-time analysis of ongoing conversations
- **Operator Support**: Actionable suggestions including follow-up questions, document requests, and issue identification

### Prerequisites

- Python 3.11+
- Node.js and npm (for frontend)
- An AssemblyAI API key (for realtime STT)
- An OpenAI API key (for AI-powered suggestions)

### 1) Clone and Setup

```bash
git clone <this-repo> rag_chatbot_legal
cd rag_chatbot_legal

python3.11 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -U pip
```

### 2) Install Backend Dependencies

```bash
pip install -r requirements.txt
```

### 3) Configure Environment Variables

Create a `.env` file in the project root:

```bash
# AssemblyAI for realtime transcription
ASSEMBLYAI_API_KEY=your_assemblyai_api_key

# OpenAI API configuration
OPENAI_API_KEY=your_openai_api_key
```

### 4) Configure Suggestion Settings

Edit `config.json` to customize suggestion behavior:

```json
{
  "suggestion_model": "gpt-3.5-turbo",
  "suggestion_temperature": 0.3,
  "max_suggestions": 3
}
```

**Note**: You can use any OpenAI model (e.g., `gpt-4`, `gpt-4-turbo`, `gpt-3.5-turbo`). The default is `gpt-3.5-turbo` for cost-effectiveness.

### 5) Customize AI Prompts

All AI prompts are stored in separate files for easy customization. Edit the files in `backend/prompts/` to modify the behavior of the agents:

**Router Agent Prompts** (controls when suggestions are generated):
- `router_system_prompt.txt` – System instructions for the router agent that decides when to generate suggestions
- `router_user_prompt.txt` – User prompt template (uses `{conversation_transcript}` placeholder)

**Suggestion Agent Prompts** (controls what suggestions are generated):
- `suggestion_system_prompt.txt` – System instructions for the suggestion agent that generates actionable recommendations
- `suggestion_user_prompt.txt` – User prompt template (uses `{conversation_transcript}` and `{max_suggestions}` placeholders)

**Fallback Suggestions** (shown when the AI fails):
- `fallback_suggestions.json` – JSON array of fallback suggestion objects to use when errors occur

**Example**: To customize the router agent's behavior, edit `backend/prompts/router_system_prompt.txt` and modify the decision criteria or instructions. The changes will take effect after restarting the backend server.

**Note**: Prompt files support template placeholders (e.g., `{conversation_transcript}`) which are automatically replaced with actual values at runtime. Do not modify these placeholders unless you understand the code structure.

### 6) Run the Backend

```bash
uvicorn backend.api:app --host 0.0.0.0 --port 8000 --reload
```

The backend provides the following endpoints:
- `GET /health` – Service health check
- `GET /config` – Configuration introspection (shows loaded keys/models)
- `POST /suggest` – AI suggestions endpoint (accepts conversation transcript)
- `POST /extract-customer-data` – Extract customer information (name, NRIC, address, purpose) from conversation transcript

Note: A backend WebSocket proxy (`WS /ws/stt`) exists but is optional. The frontend now connects directly to AssemblyAI for the best latency; the proxy can be used only if your environment requires server-side brokering.

**Backend Logging**: The suggestions endpoint provides detailed logging:
- Input conversation transcripts with character counts
- API call details (model, request parameters)
- Output suggestions with type, text, confidence, priority, and follow-up questions
- Error traces and fallback suggestions

### 7) Run the Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend runs at `http://localhost:3000` and automatically connects to the backend at `http://localhost:8000`.

**Usage**:
1. Paste your AssemblyAI API key in the header
2. Click "Start" to begin transcription (browser will request microphone access)
3. Speak – partial transcript appears instantly; when finalized it overwrites the partial (no duplicates)
4. AI suggestions appear in real time on the right, driven by a two‑agent pipeline on the backend
5. Suggestions are generated from finalized transcript turns only and update automatically

### How It Works

1. **Real-Time Transcription (Frontend)**: The frontend streams audio directly to AssemblyAI over WebSocket and renders partial text immediately; final text overwrites the partial to avoid duplicates.
2. **Transcript Analysis (Backend)**: Finalized transcript turns are posted to the `/suggest` endpoint.
3. **AI Suggestions (Two‑Agent Pipeline)**:
   - **Router Agent**: Analyzes conversation and decides when suggestions are needed
   - **Suggestion Agent**: Generates actionable recommendations including:
     - Follow-up questions to gather essential information
     - Legal issue identification
     - Document requests
     - Urgency assessment
     - Natural language responses for operators to use
4. **Customer Data Extraction**: The `/extract-customer-data` endpoint can extract structured information (name, NRIC, address, purpose) from conversation transcripts using AI.

### Troubleshooting

- **Microphone not working**: Check browser permissions and ensure your AssemblyAI API key is present in the UI.
- **Suggestions not appearing**: Verify `OPENAI_API_KEY` is configured in your `.env` file and that you have sufficient OpenAI API credits.
- **Duplicate lines in conversation**: The UI normalizes final vs partial and replaces duplicates automatically. If you still see duplicates, refresh and try again.
- **Backend errors**: Check FastAPI logs; `/suggest` logs input and output details.
- **Frontend connection issues**: Ensure the frontend can reach AssemblyAI WebSocket (corporate networks may require allowing wss to `streaming.assemblyai.com`).

### Optional: RAG Setup with Pinecone

For enhanced legal document retrieval, you can set up a Pinecone vector database:

**Install RAG dependencies**:
```bash
pip install PyPDF2 langchain_text_splitters langchain-core langchain-openai langchain-pinecone pinecone-client
```

**Add to `.env`**:
```
PINECONE_API_KEY=your_pinecone_key
PINECONE_INDEX_NAME=legal-documents
PINECONE_REGION=us-east-1
PINECONE_CLOUD=aws
```

**Process and index documents**:
```bash
cd database_setup
python pinecone_setup.py
```

### Project Structure

**Backend** (`backend/`):
- `api.py` – FastAPI application and route configuration
- `suggestions.py` – Two‑agent AI suggestion endpoint (Router → Suggestion)
- `router_agent.py` – Router agent that decides when to generate suggestions
- `suggestion_agent.py` – Suggestion agent that generates actionable recommendations
- `customer_data_extractor.py` – Extracts customer information from conversation transcripts
- `prompt_loader.py` – Utility for loading prompts from external files
- `assemblyai_ws.py` – Optional WebSocket proxy for AssemblyAI (not required for default flow)
- `config.py` – Environment and configuration management
- `prompts/` – Directory containing all editable prompt files (see section 5)

**Frontend** (`frontend/`):
- `app/page.tsx` – Main conversation UI with real-time transcription and suggestions
- `app/layout.tsx` – Next.js layout configuration

**Configuration**:
- `config.json` – Suggestion model and behavior settings
- `.env` – Environment variables (not tracked in git)
- `backend/prompts/` – Editable prompt files for customizing AI agent behavior

### Notes

- CORS is configured permissively for development (`allow_origins=["*"]`). Restrict this for production.
- The backend provides comprehensive logging for all suggestion requests, making it easy to debug and monitor the system.
- Suggestions are generated in real time from finalized transcript turns and update automatically.
- The system is optimized for Pro Bono SG's workflow, providing context-aware recommendations for legal assistance operators.
