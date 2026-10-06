# Real-Time Conversation Intelligence

Live assistant for staff on legal-assistance calls. It transcribes the call, works out who is speaking,
recognises the caller from their phone number or email, pulls up their earlier cases and suggests what staff
should say next.

The backend runs on the AWS GPU server `rci-gpu`. The frontend runs on your laptop and reaches the backend
through an SSH tunnel.

## Start the demo

### One-time laptop setup

- The AWS CLI is configured with access to the account (`aws sts get-caller-identity` works).
- The SSH key is at `~/.ssh/rci-gpu.pem`.
- Node.js 22+ is installed, and you have run `cd frontend && npm install`.

### Every time

Run these from the repo root.

1. **Start the server** (skip this step if `scripts/gpu.sh status` already says `running`):

   ```bash
   scripts/gpu.sh start
   ```

   This takes about a minute. The server costs about US$1/hour while it runs.

2. **Deploy**, but only if the code changed since the last deploy:

   ```bash
   scripts/gpu.sh deploy
   ```

   It finishes by printing `/ready`. Check that it shows `"diarization":{"ready":true,"mode":"low_latency"}`.

3. **Open the tunnel**, in its own terminal, and leave it open:

   ```bash
   scripts/gpu.sh connect
   ```

   If it says port 8000 is in use, a local backend is still running. Stop it (`ss -ltnp | grep 8000` shows
   the process) and connect again.

4. **Start the frontend**, in a second terminal:

   ```bash
   cd frontend && npm run dev
   ```

5. **Open <http://localhost:3000>** and click **Start session**. Speak with two voices: the first voice is Staff.

6. **When you're done**, press Ctrl+C in both terminals, then stop the server:

   ```bash
   scripts/gpu.sh stop
   ```

   If you forget, it stops itself after 90 minutes without use and at 02:00 SGT. Both limits are set in
   `scripts/gpu_host.conf`.

## Running a call

- The first new voice is Staff. If the roles come out wrong, click **Swap roles**, or click a single turn to
  switch it.
- When the caller gives a phone number ("nine one two three, four five six seven") or an email ("katherine dot
  liao at gmail dot com"), the caller card fills from the database and the suggestions cite their cases.
- **End call** drafts the wrap-up. Review it, then click **Save to case system**. The next call from that
  caller picks up the saved case.
- **New call** clears the screen for the next caller.

### Demo storylines

You play the caller. Follow-up dates are relative to the day the data was seeded.

1. **New caller → filing → follow-up.** "My boss hasn't paid my salary for two months." Give a new number
   such as 8111 2222. The assistant asks for your details, finds no record, asks which months were unpaid and
   whether you still work there, then advises a TADM salary claim with its deadline and documents, and agrees
   a follow-up date. Click End call, then Save. Then click **New call** and call again from the same number:
   the record shows the case you just saved, and the assistant asks whether you filed the claim.
2. **Returning caller with an overdue action.** Rajesh Kumar (8234 5678), about back-to-back shifts. The
   assistant first asks about the medical report that is overdue on his work-permit case, then advises on
   working hours (a MOM report or a TADM overtime claim).
3. **Repeat employer.** Katherine Liao (9123 4567): her pay was cut after she complained about forfeited
   leave. The assistant raises her open Brightpath leave case, links the new issue to it and advises a TADM
   claim.

**Reset the demo data** (this removes cases saved during demos). Get the IP from `scripts/gpu.sh status`,
then run:

```bash
ssh -i ~/.ssh/rci-gpu.pem ubuntu@<ip> sudo systemctl restart rci-demo-db rci-backend
```

A deploy also resets the data.

**Rehearse without a microphone** (needs the local Python venv, see Development): with the tunnel open, run
`python scripts/simulate_call.py --story all --save`. An LLM plays each caller, and the script checks each
stage.

## Troubleshooting

| Problem                                                | What to do                                                                                                                     |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| `start`, `deploy` or `connect` times out on SSH        | The server's security group probably only allows your home IP. Add your current IP to its inbound SSH rule in the AWS console. |
| `start` fails with `InsufficientInstanceCapacity`      | AWS has no spare T4 GPUs in that zone right now. Try again later; capacity usually comes back within hours.                    |
| `deploy` says the backend is not ready after 3 minutes | Read the logs: `ssh -i ~/.ssh/rci-gpu.pem ubuntu@<ip> journalctl -u rci-backend -n 80`.                                        |
| The page says it cannot reach the backend              | Check that the tunnel terminal is still open, then run `curl -s localhost:8000/ready`.                                         |
| Turns stay on _identifying speaker_ for a long time    | You are probably on a local backend, not the GPU one. `/ready` should show `"mode":"low_latency"`.                             |
| The page looks out of date                             | An old `next start` may be holding port 3000. Stop it and run `npm run dev` again.                                             |

## How it works

```text
Browser mic ──PCM──▶ WS /ws/stt (backend relay) ──▶ AssemblyAI streaming        → the words
                                                └─▶ Nemotron 3 Diarization     → who said each word
           ◀── finished turns, split wherever the speaker changes (Staff / Customer)

Each customer turn ──▶ POST /assist (NDJSON stream, backend/orchestrator.py)
  1. instant regex on the turn (phone, email, "my name is …") ──▶ customer DB lookup → caller card, prior cases
  2. in parallel:
       entity agent      LLM extraction of name / phone / email / purpose
       suggestion agent  transcript + customer record + service guide ──▶ what Staff should say next

End call ──▶ POST /wrapup   case note, agreed actions, follow-up date, message to the caller
Save     ──▶ POST /cases    creates or updates the case in the demo DB
```

- **Identity.** A phone or email match counts as verified. A match on name alone is shown as a _possible
  match_, and the assistant asks for a phone number or email before discussing cases. NRICs and addresses are
  deliberately not collected.
- **Service guide.** The `issue_guides` table holds the centre's advice for each issue type (route, deadline,
  documents, follow-up). **The demo guides are general guidance written for this demo: have the centre's legal
  team check them before real use.**
- **Prompts** are in `backend/prompts/<agent>/` (`system.md` and `user.md`). Edit them there, then deploy.
- **Settings** are listed with their defaults in `backend/config.py`. The GPU server keeps its own `.env`,
  model weights and demo DB, and `deploy` never overwrites them.

| Endpoint                 | What it does                                                       |
| ------------------------ | ------------------------------------------------------------------ |
| `GET /ready`             | LLM and transcription configured, diariser loaded, customer DB set |
| `GET /stt/session`       | One-time ticket for the relay                                      |
| `WS /ws/stt`             | The relay                                                          |
| `POST /assist`           | Both agents for one customer turn, as an NDJSON event stream       |
| `POST /customer-history` | Manual lookup by contact number, email and/or name                 |
| `POST /wrapup`           | End-of-call wrap-up                                                |
| `POST /cases`            | Save a reviewed wrap-up as a new or updated case (demo DB only)    |

## Development

You only need a local Python environment to run the tests or `simulate_call.py`:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                       # ASSEMBLYAI_API_KEY, LLM_API_KEY, LLM_BASE_URL
```

```bash
pytest                                    # backend tests
ruff check . && ruff format --check .     # backend lint / format
cd frontend && npm test                   # frontend tests
npm run format:check && npm run build     # frontend format / build
```

CI runs all of these on every pull request.

**Setting up a new GPU server:** copy the repo to it, run `scripts/setup_gpu_host.sh` (venv, CUDA torch,
model weights), add a `.env` with `DIARIZATION_DEVICE=cuda`, then run `sudo scripts/install_gpu_services.sh`
(systemd services and auto-stop).

## License

MIT, see [LICENSE](LICENSE).
