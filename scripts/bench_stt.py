"""Benchmark live-transcription latency and partial cadence against a demo WAV.

Streams the WAV to AssemblyAI in real time (like a microphone) and reports, per
model/config:
  - final latency: time from the last word's end (audio clock) to receiving the
    formatted end-of-turn message, per turn (p50 / max)
  - partial cadence: how often in-progress text updates arrive while someone speaks
  - key-term accuracy: whether names / the NRIC were transcribed as expected

Usage:
  realtime-venv/bin/python scripts/bench_stt.py [--scenario katherine_liao_brightpath]
      [--wav data/demo_audio/<scenario>.wav] [--configs u3,universal,universal-fast]
Requires the backend running (uses /assemblyai-token for a temporary token + keyterms).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import urllib.parse
import urllib.request
import wave
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
CHUNK_MS = 50

# name -> (speech_model, extra query params, send STT prompt?)
CONFIGS = {
    "u3": ("u3-rt-pro", {}, True),
    "u3-min-latency": ("u3-rt-pro", {"mode": "min_latency"}, True),
    "u3-fast": ("u3-rt-pro", {"min_end_of_turn_silence_when_confident": "160", "max_turn_silence": "800"}, True),
    "u3-snappy": ("u3-rt-pro", {"min_end_of_turn_silence_when_confident": "240", "max_turn_silence": "1000"}, True),
    "u3-max-accuracy": ("u3-rt-pro", {"mode": "max_accuracy"}, True),
    "universal": ("universal-streaming-english", {}, False),
    "universal-fast": (
        "universal-streaming-english",
        {"min_end_of_turn_silence_when_confident": "160", "max_turn_silence": "800"},
        False,
    ),
}


def fetch_token(backend: str, scenario: str) -> dict:
    with urllib.request.urlopen(f"{backend}/assemblyai-token?scenario={scenario}") as resp:
        payload = json.load(resp)
    if "token" not in payload:
        raise SystemExit(
            "The backend offered the Nemotron relay, not an AssemblyAI token: this benchmark measures the direct "
            "path, so run it against a backend without DIARIZATION_BACKEND=nemotron"
        )
    return payload


async def run_config(name: str, wav_path: Path, backend: str, scenario: str, expect_terms: list[str]) -> dict:
    model, extra, use_prompt = CONFIGS[name]
    tok = fetch_token(backend, scenario)
    with wave.open(str(wav_path)) as wav:
        rate = wav.getframerate()
        pcm = wav.readframes(wav.getnframes())
    params = {
        "sample_rate": str(rate),
        "format_turns": "true",
        "speaker_labels": "true",
        "max_speakers": "2",
        "token": tok["token"],
        "speech_model": model,
        **extra,
    }
    if tok.get("keyterms_prompt"):
        params["keyterms_prompt"] = json.dumps(tok["keyterms_prompt"])
    if use_prompt and tok.get("prompt"):
        params["prompt"] = tok["prompt"]

    url = "wss://streaming.assemblyai.com/v3/ws?" + urllib.parse.urlencode(params)
    finals: list[dict] = []
    partial_gaps: list[float] = []
    state = {"t0": None, "last_partial": None, "partials": 0, "begin": None}
    bytes_per_chunk = int(rate * CHUNK_MS / 1000) * 2

    async with websockets.connect(url, max_size=None) as ws:
        async def send() -> None:
            for i in range(0, len(pcm), bytes_per_chunk):
                if state["t0"] is None:
                    state["t0"] = time.perf_counter()
                await ws.send(pcm[i : i + bytes_per_chunk])
                target = state["t0"] + (i // bytes_per_chunk + 1) * CHUNK_MS / 1000
                await asyncio.sleep(max(0.0, target - time.perf_counter()))
            await asyncio.sleep(2.5)  # let the last turn finish
            await ws.send(json.dumps({"type": "Terminate"}))

        async def receive() -> None:
            async for raw in ws:
                now = time.perf_counter()
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "Begin":
                    state["begin"] = msg.get("configuration", {})
                elif kind == "Turn" and state["t0"] is not None:
                    words = msg.get("words") or []
                    if not msg.get("end_of_turn"):
                        if msg.get("transcript"):
                            state["partials"] += 1
                            if state["last_partial"] is not None:
                                partial_gaps.append(now - state["last_partial"])
                            state["last_partial"] = now
                        continue
                    if msg.get("turn_is_formatted") is False:
                        continue  # the app waits for the formatted version
                    state["last_partial"] = None
                    audio_end_ms = max((w.get("end") or 0) for w in words) if words else None
                    latency = (now - state["t0"]) * 1000 - audio_end_ms if audio_end_ms else None
                    finals.append({"text": msg.get("transcript", ""), "latency_ms": latency})
                elif kind == "Termination":
                    return
                elif kind == "Error":
                    raise RuntimeError(msg)

        await asyncio.gather(send(), receive())

    latencies = [f["latency_ms"] for f in finals if f["latency_ms"] is not None]
    text = " ".join(f["text"] for f in finals)
    return {
        "name": name,
        "model": (state["begin"] or {}).get("model"),
        "turns": len(finals),
        "final_p50": statistics.median(latencies) if latencies else None,
        "final_max": max(latencies) if latencies else None,
        "partials": state["partials"],
        "partial_gap_p50": statistics.median(partial_gaps) * 1000 if partial_gaps else None,
        "terms": {t: (t.lower() in text.lower()) for t in expect_terms},
        "finals": finals,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="katherine_liao_brightpath")
    parser.add_argument("--wav", default=None)
    parser.add_argument("--backend", default="http://localhost:8000")
    parser.add_argument("--configs", default="u3,u3-min-latency,universal")
    parser.add_argument("--show-text", action="store_true")
    args = parser.parse_args()

    wav = Path(args.wav or ROOT / "data" / "demo_audio" / f"{args.scenario}.wav")
    scenario = json.loads((ROOT / "demo" / "scripts" / f"{args.scenario}.json").read_text())
    terms = [scenario.get("staff_name", ""), scenario["persona"]["name"], scenario["persona"]["nric"], "Brightpath"]
    terms = [t for t in terms if t]

    results = await asyncio.gather(
        *(run_config(c.strip(), wav, args.backend, args.scenario, terms) for c in args.configs.split(",")),
        return_exceptions=True,
    )
    print(f"{'config':<15} {'model':<28} {'turns':>5} {'final p50':>10} {'final max':>10} {'partials':>9} {'partial gap':>12}  key terms")
    for r in results:
        if isinstance(r, Exception):
            print("ERROR", r)
            continue
        fmt = lambda v: f"{v:.0f}ms" if v is not None else "-"
        terms_ok = " ".join(("✓" if ok else "✗") + t for t, ok in r["terms"].items())
        print(f"{r['name']:<15} {str(r['model']):<28} {r['turns']:>5} {fmt(r['final_p50']):>10} {fmt(r['final_max']):>10} {r['partials']:>9} {fmt(r['partial_gap_p50']):>12}  {terms_ok}")
        if args.show_text:
            for f in r["finals"]:
                print(f"    {fmt(f['latency_ms']):>7}  {f['text'][:90]}")


if __name__ == "__main__":
    asyncio.run(main())
