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
        "Sure, it's S8823451D.",
        "Sure, it's S, eight eight two three four five one, D.",
        "s 8823451 d",
        "S double eight two three four five one D",
        "it's s 88 234 51 d",
    ],
)
def test_extract_nric_spoken_and_written_forms(text):
    assert extract_nric(text) == "S8823451D"


def test_extract_nric_fin_and_non_matches():
    assert extract_nric("my permit is G 6690312 P") == "G6690312P"
    assert extract_nric("I have 3 kids and 12 days of leave") is None
    assert extract_nric("") is None


def test_extract_nric_does_not_enforce_checksum():
    # S8823451D fails the official checksum but is the demo persona's ID.
    assert extract_nric("S8823451D") == "S8823451D"


def test_extract_nric_from_transcript_uses_customer_lines_only():
    transcript = "Staff: Is it S1234567A?\nCustomer: No, it's S8823451D."
    assert extract_nric_from_transcript(transcript) == "S8823451D"
    assert extract_nric_from_transcript("Staff: Is it S1234567A?") is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hi Daniel, my name is Sarah Lim.", "Sarah Lim"),
        ("This is Rajesh Kumar calling", "Rajesh Kumar"),
        ("I'm Maria Santos", "Maria Santos"),
        ("I am Calling About my salary", None),
        ("my name is sarah", None),
    ],
)
def test_extract_intro_name(text, expected):
    assert extract_intro_name(text) == expected
