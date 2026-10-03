"""CPU feasibility spike for NVIDIA Nemotron 3 Diarization (streaming), Phase 0.

Replays a WAV through the Hugging Face streaming loop chunk by chunk and reports, per
latency profile / thread count / precision:
  - per-step compute (p50 / p95), measured once the speaker cache and FIFO are full
  - real-time factor (step compute / audio advanced per step); < 1 keeps up with live audio
  - peak RSS
  - agreement of streaming speaker activity with the offline (full-file) pass

Gate for running it on this laptop: RTF <= 0.3 at low_latency (1.04 s) with 4 threads,
and step p95 <= 250 ms.

Usage (separate venv, see the plan):
  diar-venv/bin/python scripts/spike_nemotron.py [--wav data/demo_audio/<scenario>.wav]
      [--modes low_latency,very_low_latency,ultra_low_latency] [--threads 1,2,4,8] [--int8]
"""

from __future__ import annotations

import argparse
import statistics
import time
import wave
from pathlib import Path

import numpy as np
import psutil
import torch
from transformers import AutoModelForAudioFrameClassification, AutoProcessor

ROOT = Path(__file__).resolve().parent.parent
MODEL_ID = "nvidia/Nemotron-3-Diarization"
LOCAL_MODEL = ROOT / "data" / "models" / "Nemotron-3-Diarization"
SR = 16_000
# Steps before this point run with a part-empty cache/FIFO and understate the steady-state cost.
WARM_S = 45.0


def load_wav_16k(path: Path) -> np.ndarray:
    with wave.open(str(path)) as wav:
        rate, channels = wav.getframerate(), wav.getnchannels()
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    if rate == SR:
        return pcm
    # Windowed-sinc polyphase resample (up L, low-pass, down M); enough for diarisation.
    g = np.gcd(rate, SR)
    up, down = SR // g, rate // g
    taps = 64 * max(up, down) + 1
    n = np.arange(taps) - taps // 2
    cutoff = 0.5 / max(up, down)
    fir = 2 * cutoff * np.sinc(2 * cutoff * n) * np.kaiser(taps, 8.0) * up
    stuffed = np.zeros(len(pcm) * up, dtype=np.float32)
    stuffed[::up] = pcm
    return np.convolve(stuffed, fir, mode="same")[::down].astype(np.float32)


def stream(model, processor, audio: np.ndarray, mode: str) -> tuple[torch.Tensor, list[tuple[float, float]]]:
    """Run one streaming session. Returns (logits [1, T, 8], [(audio_time_s, step_ms)]).

    `mode` is a named profile or a custom "<chunk>x<right_context>" in 80 ms encoder frames, e.g. "40x4".
    """
    if mode not in processor.streaming_modes:
        chunk, right = (int(v) for v in mode.split("x"))
        processor.streaming_modes = {**processor.streaming_modes, mode: (chunk, right)}
    processor.set_streaming_mode(mode)
    fe = processor.feature_extractor
    step_mel = processor.num_mel_frames_per_step
    cache = None
    logits, timings = [], []
    mel_idx = 0
    first = True
    while True:
        if first:
            start, size = 0, processor.num_samples_first_audio_chunk
        else:
            start, size = processor.audio_chunk_start(mel_idx), processor.num_samples_per_audio_chunk
        last = start + size > len(audio)
        chunk = audio[start:] if last else audio[start : start + size]
        inputs = processor(
            chunk, sampling_rate=SR, is_streaming=True, is_first_audio_chunk=first, is_last_audio_chunk=last
        )
        t0 = time.perf_counter()
        out = model(**inputs, speaker_cache=cache)
        timings.append(((mel_idx + step_mel) * fe.hop_length / SR, (time.perf_counter() - t0) * 1000))
        cache = out.speaker_cache
        logits.append(out.logits)
        if last:
            break
        mel_idx += step_mel
        first = False
    return torch.cat(logits, dim=1), timings


def summarise(name: str, processor, audio, logits, timings, offline_active) -> dict:
    stride_ms = processor.num_mel_frames_per_step * 10
    warm = [ms for t, ms in timings if t >= WARM_S] or [ms for _, ms in timings]
    p50, p95 = statistics.median(warm), float(np.percentile(warm, 95))
    active = logits[0].sigmoid() > 0.5
    n = min(len(active), len(offline_active))
    agree = (active[:n] == offline_active[:n]).float().mean().item()
    speakers = int(active.any(dim=0).sum())
    row = {
        "config": name,
        "latency_ms": processor.streaming_latency_ms,
        "step_p50_ms": p50,
        "step_p95_ms": p95,
        "rtf": p50 / stride_ms,
        "agree_vs_offline": agree,
        "speakers": speakers,
        "rss_mb": psutil.Process().memory_info().rss / 2**20,
    }
    print(
        f"{name:<34} buf={row['latency_ms']:>5}ms step p50={p50:7.1f}ms p95={p95:7.1f}ms "
        f"RTF={row['rtf']:.2f} agree={agree:.3f} spk={speakers} rss={row['rss_mb']:.0f}MB",
        flush=True,
    )
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", default=str(ROOT / "data" / "demo_audio" / "katherine_liao_brightpath.wav"))
    parser.add_argument("--modes", default="low_latency,very_low_latency,ultra_low_latency")
    parser.add_argument("--threads", default="4")
    parser.add_argument("--repeat", type=int, default=2, help="loop the audio so the cache reaches steady state")
    parser.add_argument("--model", default=str(LOCAL_MODEL) if LOCAL_MODEL.exists() else MODEL_ID)
    parser.add_argument("--fifo", type=int, default=None, help="override the streaming FIFO length (encoder frames)")
    parser.add_argument("--int8", action="store_true", help="also try int8 dynamic quantisation of Linear layers")
    parser.add_argument("--segments", action="store_true", help="print streaming segments for the first mode")
    args = parser.parse_args()

    torch.set_num_interop_threads(1)
    one = load_wav_16k(Path(args.wav))
    audio = np.concatenate([one] * args.repeat)
    print(f"audio: {len(one) / SR:.1f}s x{args.repeat} = {len(audio) / SR:.1f}s at 16 kHz")

    processor = AutoProcessor.from_pretrained(args.model)
    model = AutoModelForAudioFrameClassification.from_pretrained(args.model).eval()
    if args.fifo:
        model.config.streaming_config.fifo_length = args.fifo
    variants = [("fp32", model)]
    if args.int8:
        try:
            q = torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
            variants.append(("int8", q))
        except Exception as exc:  # torch.ao may be unavailable in newer torch builds
            print(f"int8 quantisation unavailable: {exc}")

    with torch.inference_mode():
        torch.set_num_threads(max(int(t) for t in args.threads.split(",")))
        t0 = time.perf_counter()
        inputs = processor(one, sampling_rate=SR)
        offline = model(**inputs).logits
        print(f"offline full-file pass: {(time.perf_counter() - t0) * 1000:.0f} ms for {len(one) / SR:.1f}s")
        offline_active = torch.cat([offline[0]] * args.repeat).sigmoid() > 0.5

        rows = []
        for vname, m in variants:
            for threads in (int(t) for t in args.threads.split(",")):
                torch.set_num_threads(threads)
                for mode in args.modes.split(","):
                    stream(m, processor, one[: SR * 5], mode)  # warm-up
                    logits, timings = stream(m, processor, audio, mode)
                    row = summarise(f"{vname} {mode} t={threads}", processor, audio, logits, timings, offline_active)
                    rows.append({**row, "mode": mode, "threads": threads})
                    if args.segments and len(rows) == 1:
                        for seg in processor.extract_speaker_dict(logits[:, : len(one) // 160])[0]:
                            print(f"   {seg['Start']:6.2f}-{seg['End']:6.2f}  speaker {seg['Speaker']}")

    ok = [
        r for r in rows
        if r["mode"] == "low_latency" and r["threads"] == 4 and r["rtf"] <= 0.3 and r["step_p95_ms"] <= 250
    ]
    print("\nGATE (low_latency, 4 threads, RTF<=0.3, p95<=250ms):", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
