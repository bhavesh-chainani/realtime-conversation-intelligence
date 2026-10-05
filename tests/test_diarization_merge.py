from __future__ import annotations

from backend.diarization.merge import (
    WordSpeaker,
    majority,
    smooth,
    split_by_speaker,
    word_speakers,
)


def activity(
    spans: list[tuple[int, int, int]], total_ms: int, speakers: int = 2
) -> list[list[float]]:
    """10 ms frames; each (start_ms, end_ms, speaker) span is active at 0.9, everything else 0.05."""
    frames = [[0.05] * speakers for _ in range(total_ms // 10)]
    for start, end, spk in spans:
        for i in range(start // 10, end // 10):
            frames[i][spk] = 0.9
    return frames


def word(text: str, start: int, end: int) -> dict:
    return {"text": text, "start": start, "end": end}


def test_words_take_the_most_active_speaker():
    probs = activity([(0, 1000, 0), (1000, 2000, 1)], 2000)
    words = [word("hello", 100, 400), word("there", 500, 900), word("hi", 1200, 1500)]
    assert [w.speaker for w in word_speakers(words, probs)] == [0, 0, 1]


def test_silent_word_takes_nearest_active_frame_with_zero_margin():
    probs = activity([(0, 500, 1)], 2000)
    labels = word_speakers([word("ok", 600, 700)], probs)
    assert labels == [WordSpeaker(1, 0.0)]


def test_word_past_the_diarised_audio_is_unattributed():
    probs = activity([(0, 500, 0)], 1000)
    assert word_speakers([word("late", 5000, 5300)], probs) == [WordSpeaker(None, 0.0)]


def test_smooth_drops_low_margin_flicker_but_keeps_confident_backchannel():
    flicker = [WordSpeaker(0, 0.8), WordSpeaker(1, 0.1), WordSpeaker(0, 0.8)]
    assert smooth(flicker) == [0, 0, 0]
    backchannel = [WordSpeaker(0, 0.8), WordSpeaker(1, 0.7), WordSpeaker(0, 0.8)]
    assert smooth(backchannel) == [0, 1, 0]


def test_smooth_fills_unattributed_words_from_neighbours():
    labels = [WordSpeaker(None, 0.0), WordSpeaker(1, 0.8), WordSpeaker(None, 0.0)]
    assert smooth(labels) == [1, 1, 1]


def test_split_merged_turn_at_speaker_change():
    words = [word("Thank", 0, 100), word("you.", 100, 200), word("Okay.", 400, 600)]
    segments = split_by_speaker(words, [0, 0, 1])
    assert [(s["speaker"], s["text"]) for s in segments] == [
        (0, "Thank you."),
        (1, "Okay."),
    ]


def test_majority_ignores_unattributed():
    assert majority([None, 1, 0, 1]) == 1
    assert majority([None]) is None
