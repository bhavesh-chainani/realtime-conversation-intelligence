"""Attribute ASR words to diariser speakers.

Pure functions over plain lists, so the API process needs neither torch nor numpy.
`probs` is the diariser's speaker activity: one row per frame of `frame_ms`, one
probability per speaker channel (Nemotron numbers channels by arrival order).
Word `start` / `end` are milliseconds on the same clock as frame 0 (the stream start).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

FRAME_MS = 10


@dataclass(frozen=True)
class WordSpeaker:
    speaker: int | None
    margin: float  # best minus second-best mean probability; 0 when unattributed


def word_speakers(
    words: list[dict],
    probs: list[list[float]],
    frame_ms: int = FRAME_MS,
    min_prob: float = 0.3,
    search_ms: int = 400,
) -> list[WordSpeaker]:
    """Speaker of each word: the channel with the highest mean activity over the word.

    A word the diariser heard as silence (short words, soft endings) takes the speaker of
    the nearest active frame within `search_ms`, with zero margin, so smoothing may revise it.
    """
    out: list[WordSpeaker] = []
    for word in words:
        lo = max(0, int(word["start"] // frame_ms))
        hi = max(lo + 1, math.ceil(word["end"] / frame_ms))
        window = probs[lo:hi]
        if window:
            means = [sum(col) / len(window) for col in zip(*window, strict=False)]
            ranked = sorted(range(len(means)), key=means.__getitem__, reverse=True)
            best = ranked[0]
            if means[best] >= min_prob:
                second = means[ranked[1]] if len(ranked) > 1 else 0.0
                out.append(WordSpeaker(best, means[best] - second))
                continue
        out.append(WordSpeaker(_nearest_active(probs, lo, hi, search_ms // frame_ms), 0.0))
    return out


def _nearest_active(
    probs: list[list[float]], lo: int, hi: int, reach: int, threshold: float = 0.5
) -> int | None:
    for dist in range(1, reach + 1):
        for idx in (lo - dist, hi - 1 + dist):
            if 0 <= idx < len(probs):
                row = probs[idx]
                best = max(range(len(row)), key=row.__getitem__)
                if row[best] >= threshold:
                    return best
    return None


def smooth(labels: list[WordSpeaker], min_margin: float = 0.25) -> list[int | None]:
    """Drop one-word flickers: a low-margin word between two words of the same other
    speaker joins them. Confident one-word turns ("Yes.") are kept."""
    speakers = [w.speaker for w in labels]
    for i in range(1, len(speakers) - 1):
        prev, nxt = speakers[i - 1], speakers[i + 1]
        if prev is not None and prev == nxt and speakers[i] != prev and labels[i].margin < min_margin:
            speakers[i] = prev
    # Unattributed words join the speaker before them (or after, at the start of a turn).
    for i in range(1, len(speakers)):
        if speakers[i] is None:
            speakers[i] = speakers[i - 1]
    for i in range(len(speakers) - 2, -1, -1):
        if speakers[i] is None:
            speakers[i] = speakers[i + 1]
    return speakers


def split_by_speaker(words: list[dict], speakers: list[int | None]) -> list[dict]:
    """Contiguous runs of one speaker: [{"speaker", "words", "text"}], in order."""
    segments: list[dict] = []
    for word, speaker in zip(words, speakers, strict=False):
        if segments and segments[-1]["speaker"] == speaker:
            segments[-1]["words"].append(word)
        else:
            segments.append({"speaker": speaker, "words": [word]})
    for seg in segments:
        seg["text"] = " ".join(str(w.get("text", "")).strip() for w in seg["words"]).strip()
    return segments


def majority(speakers: list[int | None]) -> int | None:
    counts: dict[int, int] = {}
    for s in speakers:
        if s is not None:
            counts[s] = counts.get(s, 0) + 1
    return max(counts, key=counts.__getitem__) if counts else None
