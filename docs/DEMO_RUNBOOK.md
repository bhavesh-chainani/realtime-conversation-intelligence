# Live demo runbook: Sarah Lim / Brightpath

A scripted two-person call. One presenter plays the operator **Daniel (Staff)** and the other plays the caller **Sarah Lim (Customer)**. They share one laptop mic. On screen, the audience sees four things:

| Capability | What the audience sees |
|---|---|
| Speaker diarisation | Every turn is labelled Staff/Customer correctly. The caption on each turn shows why, e.g. `script L08 · 0.92` or `diarised B`. |
| Latency | A suggestion appears about 1.3 s after the customer stops speaking: either a prepared card (`Instant · prepared`) or the live one (`Live · 1.9s`). |
| Database retrieval | The NRIC fills in the moment it is spoken. Name and address then fill in as **Verified from records**, a "Returning customer · 2 prior cases, 1 open" banner appears, and the case table shows up. |
| Suggestions linked to records | Cards cite the cases they rely on: **From records: CASE-2026-03117 (Open)**. |

## 1. Start-up (about 3 minutes, in this order)

```bash
# 1. Demo database (re-run after every reboot; idempotent)
realtime-venv/bin/python scripts/demo_db.py

# 2. Backend: single worker, no --reload (keeps the warm cache and connections stable)
realtime-venv/bin/uvicorn backend.api:app --port 8000

# 3. Frontend: production build (no dev compile pauses, no StrictMode double effects)
cd frontend && npm run build && npm run start
```

Required settings. Put these in `.env`; `.env.example` lists them all.

```
DEMO_MODE=true
LLM_REASONING_EFFORT=none        # mini at ~1.9s p50 vs ~3.5s at default effort
LLM_MAX_RETRIES=1
SUGGESTION_TIMEOUT_SECONDS=6
RATE_LIMIT_PER_MINUTE=600        # the default 30/min returns 429 mid-demo
DAILY_REQUEST_QUOTA=50000
CUSTOMER_HISTORY_EXTRA_COLUMNS=address
```

In `frontend/.env.local`, set `NEXT_PUBLIC_DEMO_MODE=true`. Alternatively, open the app with `?demo=1`.

4. Open **http://localhost:3000/?demo=1** and select **Sarah Lim · salary deduction after a leave complaint**.
5. Click the cache chip to build the cache. It should read **Prepared 7/7** in about 10 s. Rebuild it after any edit to the prompts, the script or the model.
6. All preflight dots must be green: **LLM · DB · STT · Cache · Limits**. Hover a dot for details, or click the dots to re-run the checks.

## 2. Pre-flight checklist

- [ ] Preflight is all green and the cache shows `Prepared 7/7` (not `stale`).
- [ ] The browser has microphone permission and the right input device is selected.
- [ ] The laptop sits between the two presenters, about 30–50 cm from each. Speak one at a time and leave a short pause between turns.
- [ ] A phone hotspot is ready as backup network.
- [ ] Rehearsal 1: **Autopilot** at 1×, the whole script.
- [ ] Rehearsal 2: **Live mic** with `?demo=1&debug=1`. Check the console tables: alignment scores should be ≥ 0.55 on scripted lines.
- [ ] Click **Reset** before the audience arrives. Reset clears the conversation and starts a new session.

## 3. The script

The teleprompter in the demo bar always shows **NEXT · speaker: line** and the line after it, so presenters can read from the screen. Small wording slips are fine. Matching is fuzzy, and the script can be skipped ahead.

| # | Speaker | Line | What to point out |
|---|---|---|---|
| L01 | Daniel | Good afternoon, thank you for calling the Employment Advice Centre. My name is Daniel. May I have your full name, please? | |
| L02 | Sarah | Hi Daniel, my name is Sarah Lim. | Name is heard and the lookup by name runs. The banner shows **Possible returning customer** (amber). The suggestion asks to verify the NRIC before discussing any records. |
| L03 | Daniel | Thank you, Ms Lim. Could I have your NRIC number so I can pull up your records? | |
| L04 | Sarah | Sure, it's S8823451D. | **NRIC fills instantly**. Name and address become *Verified from records*. The banner turns green: 2 prior cases, 1 open. |
| L05 | Daniel | Thank you. Can I just confirm your address? | |
| L06 | Sarah | It's 12 Tampines Street 45, #08-112, Singapore 520012. | Address matches the records. The suggestion asks whether the call is about the open leave case. |
| L07 | Daniel | That matches our records. How can I help you today? | |
| L08 | Sarah | It's about my employer again, Brightpath Logistics. My September salary came in four hundred and fifty dollars short… admin penalty… | **Repeat employer.** Linked to `CASE-2025-10421`. The suggestion asks for the payslip or a written reason. |
| L09 | Daniel | I'm sorry to hear that. Did you get a payslip or anything in writing explaining the deduction? | |
| L10 | Sarah | The payslip just says admin penalty… started after I complained about my annual leave… cut my shifts from five days to three. | **Key moment: possible retaliation.** Linked to the open case `CASE-2026-03117`. The suggestion is to link this to that case rather than open a duplicate. |
| L11 | Daniel | I can see you still have an open leave case with us against Brightpath. When did the shift cut start…? | |
| L12 | Sarah | About two weeks after I filed it… "people who make trouble don't get full shifts"… WhatsApp message. | Evidence: ask for the screenshot and payslips, and go to mediation as in the earlier case. Both cases are linked. |
| L13 | Daniel | That message is really important. Please send us a screenshot and your last three payslips… | |
| L14 | Sarah | Okay, thank you. Will it take as long as last time? | Expectations are set from the earlier outcome (SGD 1,840 recovered at mediation). |

After L14, the presenters can ad-lib a closing. The captions switch to `diarised A/B`, which shows the system still handles speech that is not in the script.

Talking points:
- **Diarisation:** the script fixes the roles, and confident matches also teach the system which voice is which. Diarisation on its own makes mistakes from a single mic: in testing it gave Sarah's NRIC line to Daniel's voice, and the script corrected it. A staff member can click any turn to flip it.
- **Latency:** "Instant · prepared" is a suggestion computed in advance for this script line. The live answer replaces it as soon as it arrives. The chip always says which one is on screen.
- **Grounding:** suggestions can only cite case IDs that really exist in the record. Others are discarded on the server. Before the NRIC is verified, no case details are shown.

## 4. Fallback ladder

| Symptom | Action |
|---|---|
| A turn has the wrong speaker | Click the turn to flip it, or use **Swap roles**. A flip also re-teaches the voice mapping. |
| Transcription stalls or an error alert appears | Click **Stop mic**, switch to **Autopilot**, then **Continue from here**. Autopilot resumes at the current script line. |
| The live suggestion is slow | Nothing to do. The prepared card appears at 1.3 s automatically. |
| The LLM gateway is down | Prepared cards still appear for every scripted customer line. |
| Lookup shows *Lookup failed* | Re-run `scripts/demo_db.py`, then click **Look up** in Case history. |
| You have to start over | Click **Reset**. |

Autopilot controls: pace **1× / 1.5× / 2×**. **Step** pauses after every line; press **Next line** or the → key to continue, which lets you narrate between lines.

## 5. Hands-free rehearsal of the live-mic path

```bash
realtime-venv/bin/python scripts/make_demo_audio.py   # writes data/demo_audio/sarah_lim_brightpath.wav (two TTS voices)
```

Launch Chrome with the WAV as the microphone, then open `/?demo=1` and click **Start mic**:

```
--use-fake-ui-for-media-stream --use-fake-device-for-media-stream \
--use-file-for-fake-audio-capture=data/demo_audio/sarah_lim_brightpath.wav%noloop
```

This runs the real path end to end: AssemblyAI STT and diarisation, alignment, lookup and suggestions.

## 6. Tuning and diagnostics

- **Latency benchmark:** `realtime-venv/bin/python scripts/bench_llm.py --runs 2 --effort none`. It prints p50/p95 and whether each beat linked the expected cases.
- **Alignment thresholds:** set in `frontend/app/lib/script-align.ts` (`ALIGN_HIGH` 0.55, `ALIGN_LOW` 0.35, `WINDOW_AHEAD` 3). Use `?debug=1` to log a score table for every turn.
- **STT:** demo sessions use `u3-rt-pro` (`DEMO_SPEECH_MODEL`) so that the scenario's context prompt is accepted. Script keyterms (Brightpath, Tampines, the NRIC) are merged into `keyterms_prompt` automatically.
- **New scenario:** add `demo/scripts/<id>.json` in the same format, seed matching data in `scripts/demo_db.py`, then build its cache.
