# Live demo runbook: Sarah Lim / Brightpath

A scripted two-person call. One presenter plays the operator **Daniel (Staff)** and the other plays the caller **Sarah Lim (Customer)**. They share one laptop mic. On screen, the audience sees:

| Capability | What the audience sees |
|---|---|
| Speaker diarisation | Every turn is labelled Staff/Customer correctly. Technical view shows why on each turn, e.g. `script L08 · 0.92` or `diarised B`. |
| Latency | A **Suggested response** appears about 1.3 s after the customer stops speaking. Technical view shows whether it is prepared or live (`Prepared` / `Live · 1.9s`). |
| Database retrieval | The NRIC fills in the moment it is spoken. Name and address then fill in with a ✓ (**Verified from case records**), and the Caller card shows "Returning customer · 2 prior cases · 1 open". |
| Suggestions linked to records | The suggestion cites the cases it relies on (**Based on: CASE-2026-03117 · Open**), and the cited case is highlighted in Prior cases. |
| Story moments | The conversation narrates milestones inline: *Identity verified*, *Returning customer*, *Linked to open case …*, *Wrap-up notes drafted*. |
| After-call work | **End call** drafts the case notes (summary, linked cases, next steps, documents requested), ready to copy. |

**Screen layout.** The whole call fits on one screen with no scrolling at 1366×768 and up, so it holds up over a laptop screen share. Presenter controls live in a small **dock** at the bottom right, so they stay out of the audience's way:

| Key | Action |
|---|---|
| **D** | Open or close the presenter dock: scenario, Live/Autopilot, pace, prepared cards, preflight, Show script |
| **P** | Pause or resume the call: listening stops, the screen stays |
| **T** | Toggle **Technical view**: attribution captions, speaker labels, Swap roles, prepared/live timing, field sources. Use it when someone technical asks "how does it know?" |
| **→** | Autopilot: play or finish the next line |

The dot on the dock summarises preflight: green means ready, amber means the prepared cards need rebuilding, and red means a check is failing (hover for details).

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

4. Open **http://localhost:3000/?demo=1**. Press **D** to open the dock and select **Sarah Lim · salary deduction after a leave complaint**.
5. In the dock, click the prepared-cards chip to build it. It should read **Prepared 8/8** (7 customer lines plus the wrap-up) in about 10 s. Rebuild it after any edit to the prompts, the script or the model.
6. All preflight dots must be green: **LLM · DB · STT · Cache · Limits**, and the dock dot must be green. Hover a dot for details, or click the dots to re-run the checks.

## 2. Pre-flight checklist

- [ ] The dock dot is green and the dock shows `Prepared 8/8` (not `stale`).
- [ ] **Technical view is off** (press **T** if captions or timing chips are visible), and the dock is closed.
- [ ] The browser has microphone permission and the right input device is selected.
- [ ] The laptop sits between the two presenters, about 30–50 cm from each. Speak one at a time and leave a short pause between turns.
- [ ] A phone hotspot is ready as backup network.
- [ ] Rehearsal 1: **Autopilot** at 1×, the whole script.
- [ ] Rehearsal 2: **Live mic** with `?demo=1&debug=1`. Check the console tables: alignment scores should be ≥ 0.55 on scripted lines.
- [ ] Click **Reset** before the audience arrives. Reset clears the conversation and the wrap-up, and starts a new session.

## 3. The script

For the printable presenter version, with pause points and talk tracks, see **[DEMO_PRESENTER_SCRIPT.md](DEMO_PRESENTER_SCRIPT.md)**.

**Pausing to explain:** press **Pause** (top right) or **P**. Listening stops, everything stays on screen, and the header reads *On hold*. Press **Resume** or **P** to continue. Don't use **End call** to pause: it ends the call and drafts the wrap-up. If you press it by mistake, click **← Back to call**.

The teleprompter is **hidden by default**, because the audience would otherwise read each line before it is said. To show it while rehearsing, open the dock (**D**) and click **Show script**; it appears inside the dock, not on the main screen. It displays **NEXT · speaker: line** and the line after it. On demo day, presenters should know the lines or read from a printout or a second screen. Script matching runs whether the teleprompter is shown or not. Small wording slips are fine: matching is fuzzy, and the script can be skipped ahead.

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

After L14, the presenters can ad-lib a closing (in Technical view the captions switch to `diarised A/B`, which shows the system still handles speech that is not in the script).

**Close with End call** (top right). The call clock stops, and the right-hand panel switches to **Wrap-up notes**: issue, summary, linked cases, next steps and documents requested. Click **Copy to case notes** and say: *"That's the after-call work done."* A prepared version appears after about 1.3 s if the live one is slow. Press **Reset** for the next run.

Talking points:
- **Diarisation:** the script fixes the roles, and confident matches also teach the system which voice is which. Diarisation on its own makes mistakes from a single mic: in testing it gave Sarah's NRIC line to Daniel's voice, and the script corrected it. A staff member can click any turn to flip it.
- **Latency:** if asked, press **T**. A "Prepared" card was computed in advance for this script line, and the live answer replaces it as soon as it arrives (usually about 2 s). The chip always says which one is on screen. The business view makes no timing claims.
- **Grounding:** suggestions can only cite case IDs that really exist in the record. Others are discarded on the server. Before the NRIC is verified, no case details are shown.

## 4. Fallback ladder

| Symptom | Action |
|---|---|
| A turn has the wrong speaker | Click the turn to flip it (or press **T** and use **Swap roles**). A flip also re-teaches the voice mapping. |
| Transcription stalls or an error alert appears | In the dock, click **Stop mic**, press **D**, switch to **Autopilot**, then click **Continue**. Autopilot resumes at the current script line. |
| The wrap-up can't be drafted | Prepared notes appear automatically for a completed script. Otherwise, take notes manually. |
| The live suggestion is slow | Nothing to do. The prepared card appears at 1.3 s automatically. |
| The LLM gateway is down | Prepared cards still appear for every scripted customer line. |
| Lookup shows *Lookup failed* | Re-run `scripts/demo_db.py`, then click **Look up** in Case history. |
| You have to start over | Click **Reset**. |

Autopilot controls (in the dock): pace **1× / 1.5× / 2×**. **Step** pauses after every line; press **Next →** or the → key to continue, which lets you narrate between lines.

## 5. Hands-free rehearsal of the live-mic path

```bash
realtime-venv/bin/python scripts/make_demo_audio.py   # writes data/demo_audio/sarah_lim_brightpath.wav (two TTS voices)
```

Launch Chrome with the WAV as the microphone, then open `/?demo=1` and click **Start mic** in the dock:

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
