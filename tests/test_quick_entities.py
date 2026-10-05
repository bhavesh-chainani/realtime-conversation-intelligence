from __future__ import annotations

import pytest

from backend.quick_entities import (
    extract_email,
    extract_email_from_transcript,
    extract_intro_name,
    extract_phone,
    extract_phone_from_transcript,
    normalize_email,
    normalize_phone,
    quick_patch,
    quick_patch_from_lines,
)


@pytest.mark.parametrize(
    "text",
    [
        "Sure, it's 91234567.",
        "My number is 9123 4567.",
        "It's +65 9123 4567",
        "9123-4567",
        "nine one two three, four five six seven",
        "nine one two three four five six seven, thanks",
    ],
)
def test_extract_phone_spoken_and_written_forms(text):
    assert extract_phone(text) == "91234567"


def test_extract_phone_repeats_and_non_matches():
    assert extract_phone("double eight two three, four five six seven") == "88234567"
    assert extract_phone("six two triple three oh one two") == "62333012"
    assert extract_phone("I have 3 kids and 12 days of leave") is None
    assert extract_phone("They paid SGD 1,840 after mediation") is None
    assert extract_phone("It's 9123 456") is None  # 7 digits
    assert extract_phone("12345678") is None  # SG numbers start with 3, 6, 8 or 9
    assert extract_phone("") is None


def test_normalize_phone():
    assert normalize_phone("+65 9123 4567") == "91234567"
    assert normalize_phone("6591234567") == "91234567"
    assert normalize_phone("9123") is None
    assert normalize_phone(None) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("It's katherine.liao@gmail.com.", "katherine.liao@gmail.com"),
        ("Katherine.Liao@Gmail.com", "katherine.liao@gmail.com"),
        ("katherine dot liao at gmail dot com", "katherine.liao@gmail.com"),
        ("my email is kliao at gmail dot com, thanks", "kliao@gmail.com"),
        ("raj underscore kumar at gmail dot com dot sg", "raj_kumar@gmail.com.sg"),
        ("I was at the office", None),
        ("I was at work dot yesterday", None),
        ("", None),
    ],
)
def test_extract_email(text, expected):
    assert extract_email(text) == expected


def test_normalize_email():
    assert normalize_email(" Katherine.Liao@Gmail.com ") == "katherine.liao@gmail.com"
    assert normalize_email("katherine at example") is None


def test_extract_from_transcript_uses_customer_lines_only():
    transcript = (
        "Staff: Is it 94567890, or david.tan@gmail.com?\n"
        "Customer: No, it's 9123 4567, katherine.liao@gmail.com."
    )
    assert extract_phone_from_transcript(transcript) == "91234567"
    assert extract_email_from_transcript(transcript) == "katherine.liao@gmail.com"
    assert extract_phone_from_transcript("Staff: Is it 91234567?") is None
    assert extract_email_from_transcript("Staff: Is it a@gmail.com?") is None


def test_quick_patch():
    assert quick_patch("I'm Katherine Liao, my number is 9123 4567.") == {
        "contact_number": "91234567",
        "name": "Katherine Liao",
    }
    assert quick_patch("My salary was cut.") == {}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hi Bhavesh, my name is Katherine Liao.", "Katherine Liao"),
        ("This is Rajesh Kumar calling", "Rajesh Kumar"),
        ("I'm Maria Santos", "Maria Santos"),
        ("I am Calling About my salary", None),
        ("my name is katherine", None),
    ],
)
def test_extract_intro_name(text, expected):
    assert extract_intro_name(text) == expected


def test_quick_patch_from_lines_latest_mention_wins():
    lines = [
        "I'm Ahmad Rahim, my boss deducted 400 dollars.",
        "My old number was 8111 2222.",
        "Sorry, it's 8111 3333 now.",
    ]
    assert quick_patch_from_lines(lines) == {
        "contact_number": "81113333",
        "name": "Ahmad Rahim",
    }
