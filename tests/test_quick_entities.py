from __future__ import annotations

import pytest

from backend.quick_entities import (
    extract_intro_name,
    extract_nric,
    extract_nric_from_transcript,
)


@pytest.mark.parametrize(
    "text",
    [
        "Sure, it's S1234567A.",
        "Sure, it's S, one two three four five six seven, A.",
        "s 1234567 a",
        "my nric is s 1,234,567 a",
        "it's s 12 345 67 a",
    ],
)
def test_extract_nric_spoken_and_written_forms(text):
    assert extract_nric(text) == "S1234567A"


def test_extract_nric_fin_and_non_matches():
    assert extract_nric("my permit is G 6690312 P") == "G6690312P"
    assert extract_nric("G double six nine zero three one two P") == "G6690312P"
    assert extract_nric("I have 3 kids and 12 days of leave") is None
    assert extract_nric("") is None


def test_extract_nric_does_not_enforce_checksum():
    # S1234567A fails the official checksum but is the demo persona's ID.
    assert extract_nric("S1234567A") == "S1234567A"


def test_extract_nric_from_transcript_uses_customer_lines_only():
    transcript = "Staff: Is it S7612094B?\nCustomer: No, it's S1234567A."
    assert extract_nric_from_transcript(transcript) == "S1234567A"
    assert extract_nric_from_transcript("Staff: Is it S1234567A?") is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hi Bhavesh, my name is Sarah Lim.", "Sarah Lim"),
        ("This is Rajesh Kumar calling", "Rajesh Kumar"),
        ("I'm Maria Santos", "Maria Santos"),
        ("I am Calling About my salary", None),
        ("my name is sarah", None),
    ],
)
def test_extract_intro_name(text, expected):
    assert extract_intro_name(text) == expected
