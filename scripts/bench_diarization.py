"""Compare speaker attribution: AssemblyAI streaming labels vs Nemotron 3 Diarization.

Ground truth comes from scripts/make_demo_audio.py (`<wav>.timeline.json`). Every word
AssemblyAI finalises is scored against the line it overlaps, so the numbers measure what
the operator sees: the Staff / Customer label on each word.

Two steps, two venvs (the API venv has no torch):

  # 1. stream each WAV to AssemblyAI in real time with the app's settings; saves <wav>.aai.json
  realtime-venv/bin/python scripts/bench_diarization.py capture data/diar_eval/*.wav

  # 2. run Nemotron over the same audio and score both systems
  diar-venv/bin/python scripts/bench_diarization.py score data/diar_eval/*.wav \
      [--configs low_latency,40x4] [--int8] [--show]

Metrics (lower is better), each as % of scored words:
  word     per-word labels, roles assigned the way the app does it: first voice = staff
  turn     every word takes its turn's majority label (the app today, without a script)
  oracle   per-word labels with the best label-to-role mapping (separation quality only)
Plus `swap` (the first-voice rule mapped the voices the wrong way round) and `mixed`
(AssemblyAI turns that contain both speakers, which only per-word labels can fix).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.parse
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

CHUNK_MS = 50
NO_LABEL = {"", "PENDING", "UNKNOWN", "NONE", "NULL"}
TRUTH_REACH_MS = 600  # words this close to a line (but outside it) still belong to it


# ---------------------------------------------------------------------------
# capture (realtime-venv)
# ---------------------------------------------------------------------------


async def capture_one(wav_path: Path) -> None:
    import websockets

    from backend import config as cfg
    from backend.assemblyai import create_streaming_token
    from backend.demo_cache import scenario_stt_config

    truth = json.loads(wav_path.with_suffix(".timeline.json").read_text())
    keyterms, prompt = scenario_stt_config(truth["scenario"])
    speech_model = cfg.ASSEMBLYAI_SPEECH_MODEL or cfg.DEMO_SPEECH_MODEL
    with wave.open(str(wav_path)) as wav:
        rate = wav.getframerate()
        pcm = wav.readframes(wav.getnframes())
    params = {
        "sample_rate": str(rate),
        "format_turns": "true",
        "speaker_labels": "true",
        "max_speakers": "2",
        "speech_model": speech_model,
        **{k: str(v) for k, v in cfg.ASSEMBLYAI_STREAM_PARAMS.items()},
    }
    if keyterms:
        params["keyterms_prompt"] = json.dumps(keyterms)
    if prompt and speech_model.startswith("u3"):
        params["prompt"] = prompt
    token = await create_streaming_token()
    url = "wss://streaming.assemblyai.com/v3/ws?" + urllib.parse.urlencode({**params, "token": token})

    messages: list[dict] = []
    bytes_per_chunk = int(rate * CHUNK_MS / 1000) * 2
    async with websockets.connect(url, max_size=None, open_timeout=60) as ws:

        async def send() -> None:
            t0 = time.perf_counter()
            for i in range(0, len(pcm), bytes_per_chunk):
                await ws.send(pcm[i : i + bytes_per_chunk])
                target = t0 + (i // bytes_per_chunk + 1) * CHUNK_MS / 1000
                await asyncio.sleep(max(0.0, target - time.perf_counter()))
            await asyncio.sleep(3.0)
            await ws.send(json.dumps({"type": "Terminate"}))

        async def receive() -> None:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "Error":
                    raise RuntimeError(msg)
                messages.append(msg)
                if msg.get("type") == "Termination":
                    return

        await asyncio.gather(send(), receive())

    out = wav_path.with_suffix(".aai.json")
    out.write_text(json.dumps({"params": params, "messages": messages}, indent=1))
    print(f"{wav_path.name}: {sum(1 for m in messages if m.get('type') == 'Turn')} Turn messages -> {out.name}")


# ---------------------------------------------------------------------------
# score (diar-venv)
# ---------------------------------------------------------------------------


def final_turns(messages: list[dict]) -> list[dict]:
    """The turns the app commits: formatted end-of-turn messages, last version per turn_order."""
    turns: dict[int, dict] = {}
    formatted = any(m.get("turn_is_formatted") for m in messages)
    for m in messages:
        if m.get("type") != "Turn" or not m.get("end_of_turn"):
            continue
        if formatted and not m.get("turn_is_formatted"):
            continue
        turns[m.get("turn_order", len(turns))] = m
    return [turns[k] for k in sorted(turns)]


def aai_label(raw) -> str | None:
    label = str(raw or "").strip().upper()
    return None if label in NO_LABEL else label


def truth_role(word: dict, segments: list[dict]) -> str | None:
    best, best_overlap = None, 0.0
    for seg in segments:
        overlap = min(word["end"], seg["end_ms"]) - max(word["start"], seg["start_ms"])
        if overlap > best_overlap:
            best, best_overlap = seg["role"], overlap
    if best:
        return best
    gaps = [
        (max(seg["start_ms"] - word["end"], word["start"] - seg["end_ms"]), seg["role"]) for seg in segments
    ]
    gap, role = min(gaps)
    return role if gap <= TRUTH_REACH_MS else None


def arrival_map(labels: list, first_role: str) -> dict:
    """The app's rule without a script: the first voice heard is `first_role`, the next the other."""
    other = "customer" if first_role == "staff" else "staff"
    mapping: dict = {}
    for label in labels:
        if label is not None and label not in mapping and len(mapping) < 2:
            mapping[label] = first_role if not mapping else other
    return mapping


def oracle_map(labels: list, truths: list[str]) -> dict:
    """Best one-to-one mapping of the two busiest labels; any others take their majority role."""
    counts: dict = {}
    for label, truth in zip(labels, truths):
        if label is not None:
            counts.setdefault(label, {"staff": 0, "customer": 0})[truth] += 1
    busiest = sorted(counts, key=lambda k: -sum(counts[k].values()))
    mapping = {k: max(v, key=v.__getitem__) for k, v in counts.items()}
    if len(busiest) >= 2:
        a, b = busiest[:2]
        if counts[a]["staff"] + counts[b]["customer"] >= counts[a]["customer"] + counts[b]["staff"]:
            mapping[a], mapping[b] = "staff", "customer"
        else:
            mapping[a], mapping[b] = "customer", "staff"
    return mapping


def error_rate(labels: list, truths: list[str], mapping: dict) -> float:
    wrong = sum(1 for label, truth in zip(labels, truths) if mapping.get(label) != truth)
    return 100.0 * wrong / max(1, len(truths))


def turn_majority(labels: list, turn_ids: list[int]) -> list:
    from backend.diarization.merge import majority

    by_turn: dict[int, list] = {}
    for label, tid in zip(labels, turn_ids):
        by_turn.setdefault(tid, []).append(label)
    winners = {tid: majority(ls) for tid, ls in by_turn.items()}
    return [winners[tid] for tid in turn_ids]


def nemotron_probs(model, processor, audio, mode: str) -> list[list[float]]:
    from spike_nemotron import stream

    logits, _ = stream(model, processor, audio, mode)
    return logits[0].sigmoid().tolist()


def score(args: argparse.Namespace) -> None:
    import torch
    from transformers import AutoModelForAudioFrameClassification, AutoProcessor

    from backend.diarization.merge import smooth, word_speakers
    from spike_nemotron import LOCAL_MODEL, MODEL_ID, load_wav_16k

    torch.set_num_threads(args.threads)
    model_path = str(LOCAL_MODEL) if LOCAL_MODEL.exists() else MODEL_ID
    processor = AutoProcessor.from_pretrained(model_path)
    model = AutoModelForAudioFrameClassification.from_pretrained(model_path).eval()
    if args.int8:
        model = torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
    configs = [c for c in args.configs.split(",") if c]

    rows = []
    for wav_path in map(Path, args.wavs):
        truth = json.loads(wav_path.with_suffix(".timeline.json").read_text())
        first_role = truth["segments"][0]["role"]
        turns = final_turns(json.loads(wav_path.with_suffix(".aai.json").read_text())["messages"])

        words, turn_ids, truths = [], [], []
        for tid, turn in enumerate(turns):
            for w in turn.get("words") or []:
                role = truth_role(w, truth["segments"])
                if role is None:
                    continue
                words.append(w)
                turn_ids.append(tid)
                truths.append(role)
        mixed = sum(1 for tid in set(turn_ids) if len({t for t, i in zip(truths, turn_ids) if i == tid}) > 1)

        systems = {"aai": [aai_label(w.get("speaker")) for w in words]}
        # The app labels a whole AssemblyAI turn with its turn-level speaker_label.
        aai_turn = [aai_label(turns[tid].get("speaker_label")) for tid in turn_ids]
        audio = load_wav_16k(wav_path)
        with torch.inference_mode():
            for mode in configs:
                probs = nemotron_probs(model, processor, audio, mode)
                systems[f"nemo-{mode}"] = smooth(word_speakers(words, probs))

        for name, labels in systems.items():
            arrival = arrival_map(labels, first_role)
            oracle = oracle_map(labels, truths)
            row = {
                "file": wav_path.stem,
                "system": name,
                "words": len(words),
                "word": error_rate(labels, truths, arrival),
                "turn": error_rate(
                    aai_turn if name == "aai" else turn_majority(labels, turn_ids), truths, arrival
                ),
                "oracle": error_rate(labels, truths, oracle),
                "swap": any(oracle.get(k) != v for k, v in arrival.items()),
                "mixed": mixed,
                "turns": len(set(turn_ids)),
                "speakers": len({label for label in labels if label is not None}),
            }
            rows.append(row)
            if args.show:
                print(f"\n== {wav_path.stem} / {name}")
                for tid, turn in enumerate(turns):
                    idx = [i for i, t in enumerate(turn_ids) if t == tid]
                    marks = " ".join(
                        f"{words[i]['text']}{'' if arrival.get(labels[i]) == truths[i] else '[x]'}" for i in idx
                    )
                    print(f"  {tid:2d} {marks}")

    print(f"\n{'file':<34} {'system':<18} {'words':>5} {'word%':>6} {'turn%':>6} {'oracle%':>7} {'swap':>5} {'mixed':>9} {'spk':>3}")
    for r in rows:
        print(
            f"{r['file']:<34} {r['system']:<18} {r['words']:>5} {r['word']:>6.1f} {r['turn']:>6.1f} "
            f"{r['oracle']:>7.1f} {'YES' if r['swap'] else '-':>5} {r['mixed']:>3}/{r['turns']:<5} {r['speakers']:>3}"
        )
    out = Path(args.wavs[0]).parent / "results.json"
    out.write_text(json.dumps({"configs": configs, "int8": args.int8, "rows": rows}, indent=2))
    print(f"\nWrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture", help="stream WAVs to AssemblyAI (realtime-venv)")
    cap.add_argument("wavs", nargs="+")
    sc = sub.add_parser("score", help="run Nemotron and score both systems (diar-venv)")
    sc.add_argument("wavs", nargs="+")
    sc.add_argument("--configs", default="low_latency,40x4")
    sc.add_argument("--threads", type=int, default=4)
    sc.add_argument("--int8", action="store_true")
    sc.add_argument("--show", action="store_true", help="print every turn with misattributed words marked [x]")
    args = parser.parse_args()

    if args.cmd == "capture":

        async def run_all() -> None:
            await asyncio.gather(*(capture_one(Path(w)) for w in args.wavs))

        asyncio.run(run_all())
    else:
        score(args)


if __name__ == "__main__":
    main()
