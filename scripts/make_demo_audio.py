"""Synthesise a demo script into a two-voice WAV (via the LLM gateway's TTS model).

Use it to rehearse or regression-test the live-mic path without two presenters:
feed the WAV to Chrome as a fake microphone (see docs/DEMO_RUNBOOK.md).

Usage:
  realtime-venv/bin/python scripts/make_demo_audio.py [--scenario sarah_lim_brightpath]
      [--out data/demo_audio/<scenario>.wav] [--model openai.tts-1]
      [--staff-voice onyx] [--customer-voice nova] [--gap-ms 900] [--lead-ms 4000]
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.demo_cache import load_scenario  # noqa: E402
from backend.llm import get_llm_client  # noqa: E402

SAMPLE_RATE = 24_000  # OpenAI-style TTS "pcm" output: 24 kHz, 16-bit, mono


def silence(ms: int) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * ms / 1000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="sarah_lim_brightpath")
    parser.add_argument("--out", default=None)
    parser.add_argument("--model", default="openai.tts-1")
    parser.add_argument("--staff-voice", default="onyx")
    parser.add_argument("--customer-voice", default="nova")
    parser.add_argument("--gap-ms", type=int, default=900)
    parser.add_argument("--lead-ms", type=int, default=4000, help="silence before the first line")
    args = parser.parse_args()

    client = get_llm_client()
    if client is None:
        sys.exit("LLM_API_KEY / LLM_BASE_URL are not configured")

    scenario = load_scenario(args.scenario)
    out = Path(args.out or ROOT / "data" / "demo_audio" / f"{args.scenario}.wav")
    out.parent.mkdir(parents=True, exist_ok=True)

    frames = [silence(args.lead_ms)]
    timeline = []
    cursor_ms = args.lead_ms
    for line in scenario["lines"]:
        voice = args.staff_voice if line["role"] == "staff" else args.customer_voice
        audio = client.audio.speech.create(
            model=args.model, voice=voice, input=line["text"], response_format="pcm"
        ).read()
        duration_ms = len(audio) / 2 / SAMPLE_RATE * 1000
        timeline.append((line["id"], line["role"], cursor_ms, duration_ms))
        frames += [audio, silence(args.gap_ms)]
        cursor_ms += duration_ms + args.gap_ms
        print(f"  {line['id']} {line['role']:<8} {duration_ms / 1000:5.1f}s")

    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(b"".join(frames))
    print(f"Wrote {out} ({cursor_ms / 1000:.0f}s)")


if __name__ == "__main__":
    main()
