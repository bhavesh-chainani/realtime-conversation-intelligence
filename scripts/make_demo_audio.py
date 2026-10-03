"""Synthesise a demo script into a two-voice WAV (via the LLM gateway's TTS model).

Use it to rehearse or regression-test the live-mic path without two presenters:
feed the WAV to Chrome as a fake microphone (see docs/DEMO_RUNBOOK.md).

Alongside the WAV it writes the ground truth for diarisation evaluation
(scripts/bench_diarization.py): `<out>.timeline.json` (speech extent of every line,
in ms) and `<out>.rttm`.

Variants:
  easy  the script as written, contrasting voices (onyx / nova), 900 ms gaps
  hard  similar voices (onyx / echo), 150-350 ms gaps, backchannels ("Mm-hm.") from
        the other speaker between lines: the turn-taking that makes diarisation swap
  room  hard, plus a laptop mic in a room: reverb, background noise, and the customer
        farther from the mic (quieter) than the staff member

Usage:
  realtime-venv/bin/python scripts/make_demo_audio.py [--scenario katherine_liao_brightpath]
      [--variant easy|hard|room] [--out data/demo_audio/<scenario>[_<variant>].wav]
      [--model openai.tts-1] [--staff-voice onyx] [--customer-voice nova] [--gap-ms 900] [--lead-ms 4000]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.demo_cache import load_scenario  # noqa: E402
from backend.llm import get_llm_client  # noqa: E402

SAMPLE_RATE = 24_000  # OpenAI-style TTS "pcm" output: 24 kHz, 16-bit, mono

VARIANTS = {
    "easy": {"staff_voice": "onyx", "customer_voice": "nova", "gap_ms": (900, 900), "backchannels": False},
    "hard": {"staff_voice": "onyx", "customer_voice": "echo", "gap_ms": (150, 350), "backchannels": True},
    "room": {"staff_voice": "onyx", "customer_voice": "echo", "gap_ms": (150, 350), "backchannels": True},
}
BACKCHANNELS = {
    "staff": ["Mm-hm.", "I see.", "Right.", "Okay."],
    "customer": ["Yes.", "Mm-hm.", "Okay.", "Right."],
}
# Samples quieter than this (about -42 dBFS) count as silence when trimming line extents.
SPEECH_FLOOR = 260


def silence(ms: int) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * ms / 1000)


def speech_extent_ms(pcm: bytes) -> tuple[float, float]:
    """Start/end of audible speech inside a TTS clip (the model pads both ends with silence)."""
    samples = memoryview(pcm).cast("h")
    loud = [i for i in range(0, len(samples), 48) if abs(samples[i]) > SPEECH_FLOOR]
    if not loud:
        return 0.0, len(samples) / SAMPLE_RATE * 1000
    return loud[0] / SAMPLE_RATE * 1000, min(len(samples), loud[-1] + 48) / SAMPLE_RATE * 1000


def with_backchannels(lines: list[dict], rng: random.Random) -> list[dict]:
    """Insert a short acknowledgement from the listener after every other line."""
    out: list[dict] = []
    for i, line in enumerate(lines):
        out.append(line)
        if i < len(lines) - 1 and i % 2 == 1:
            listener = "customer" if line["role"] == "staff" else "staff"
            out.append({"id": f"{line['id']}b", "role": listener, "text": rng.choice(BACKCHANNELS[listener])})
    return out


def simulate_room(pcm: bytes, roles: list[tuple[int, int, str]], seed: int) -> bytes:
    """Laptop mic in a small room: customer attenuated, ~0.35 s reverb tail, noise at ~20 dB SNR."""
    import numpy as np

    rng = np.random.default_rng(seed)
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    for start, end, role in roles:
        if role == "customer":
            x[start:end] *= 0.45
    rir_len = int(0.35 * SAMPLE_RATE)
    rir = rng.standard_normal(rir_len) * np.exp(-6.9 * np.arange(rir_len) / rir_len)
    rir[0] = 1.0 / 0.4
    rir *= 0.4 / np.sqrt(np.sum(rir**2))
    wet = np.fft.irfft(np.fft.rfft(x, len(x) + rir_len) * np.fft.rfft(rir, len(x) + rir_len))[: len(x)]
    spectrum = np.fft.rfft(rng.standard_normal(len(x)))
    spectrum[0] = 0.0
    spectrum[1:] /= np.sqrt(np.arange(1, len(spectrum)))  # pink noise: room hum and fan
    noise = np.fft.irfft(spectrum, len(x))
    speech_rms = np.sqrt(np.mean(wet[np.abs(wet) > 0.01] ** 2))
    noise *= speech_rms / (10 ** (20 / 20)) / np.sqrt(np.mean(noise**2))
    y = np.clip(wet + noise, -1.0, 1.0)
    return (y * 32767).astype(np.int16).tobytes()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="katherine_liao_brightpath")
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="easy")
    parser.add_argument("--out", default=None)
    parser.add_argument("--model", default="openai.tts-1")
    parser.add_argument("--staff-voice", default=None)
    parser.add_argument("--customer-voice", default=None)
    parser.add_argument("--gap-ms", type=int, default=None, help="fixed gap between lines (overrides the variant)")
    parser.add_argument("--lead-ms", type=int, default=4000, help="silence before the first line")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    preset = VARIANTS[args.variant]
    staff_voice = args.staff_voice or preset["staff_voice"]
    customer_voice = args.customer_voice or preset["customer_voice"]
    gap_range = (args.gap_ms, args.gap_ms) if args.gap_ms is not None else preset["gap_ms"]
    rng = random.Random(args.seed)

    client = get_llm_client()
    if client is None:
        sys.exit("LLM_API_KEY / LLM_BASE_URL are not configured")

    scenario = load_scenario(args.scenario)
    suffix = "" if args.variant == "easy" else f"_{args.variant}"
    out = Path(args.out or ROOT / "data" / "demo_audio" / f"{args.scenario}{suffix}.wav")
    out.parent.mkdir(parents=True, exist_ok=True)

    lines = scenario["lines"]
    if preset["backchannels"]:
        lines = with_backchannels(lines, rng)

    frames = [silence(args.lead_ms)]
    timeline = []
    spans: list[tuple[int, int, str]] = []
    cursor_ms = float(args.lead_ms)
    for line in lines:
        voice = staff_voice if line["role"] == "staff" else customer_voice
        audio = client.audio.speech.create(
            model=args.model, voice=voice, input=line.get("say", line["text"]), response_format="pcm"
        ).read()
        duration_ms = len(audio) / 2 / SAMPLE_RATE * 1000
        on_ms, off_ms = speech_extent_ms(audio)
        timeline.append(
            {
                "id": line["id"],
                "role": line["role"],
                "text": line["text"],
                "start_ms": round(cursor_ms + on_ms),
                "end_ms": round(cursor_ms + off_ms),
            }
        )
        start_sample = int(cursor_ms * SAMPLE_RATE / 1000)
        spans.append((start_sample, start_sample + len(audio) // 2, line["role"]))
        gap = rng.randint(*gap_range)
        frames += [audio, silence(gap)]
        cursor_ms += duration_ms + gap
        print(f"  {line['id']:<4} {line['role']:<8} {duration_ms / 1000:5.1f}s  {line['text'][:60]}")

    pcm = b"".join(frames)
    if args.variant == "room":
        pcm = simulate_room(pcm, spans, args.seed)

    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)

    truth = {
        "scenario": args.scenario,
        "variant": args.variant,
        "voices": {"staff": staff_voice, "customer": customer_voice},
        "segments": timeline,
    }
    out.with_suffix(".timeline.json").write_text(json.dumps(truth, indent=2))
    with out.with_suffix(".rttm").open("w") as rttm:
        for seg in timeline:
            dur = (seg["end_ms"] - seg["start_ms"]) / 1000
            rttm.write(f"SPEAKER {out.stem} 1 {seg['start_ms'] / 1000:.3f} {dur:.3f} <NA> <NA> {seg['role']} <NA> <NA>\n")
    print(f"Wrote {out} ({cursor_ms / 1000:.0f}s) + .timeline.json + .rttm")


if __name__ == "__main__":
    main()
