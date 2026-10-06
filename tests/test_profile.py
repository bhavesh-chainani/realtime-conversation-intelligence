from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.profile import (
    Profile,
    cases_key,
    next_lookup,
    records_prefill,
)

PRECEDENCE = json.loads((Path(__file__).parent / "fixtures" / "profile_precedence.json").read_text())


@pytest.mark.parametrize("case", PRECEDENCE, ids=[c["case"] for c in PRECEDENCE])
def test_field_precedence(case):
    """Shared with frontend/app/lib/customer-profile.test.ts so both sides apply the same rules."""
    profile = Profile.from_request(case["values"], case["sources"])
    assert profile.apply(case["patch"], case["source"]) == case["accepted"]


def test_missing_and_suggestion_payload():
    profile = Profile.from_request({"name": "Katherine Liao"}, {"name": "heard"})
    assert profile.missing() == ["contact_number", "email", "purpose_of_call"]
    assert profile.suggestion_payload("email") == {
        "name": "Katherine Liao",
        "record_match": "email",
    }
    assert profile.suggestion_payload(None) == {"name": "Katherine Liao"}


def test_lookup_guard_phone_or_email_only_no_repeats():
    by_phone = next_lookup(None, "+65 9123 4567", "")
    assert by_phone == ("id:91234567", "91234567", None)
    assert next_lookup(by_phone.key, "9123 4567", "") is None
    # Hearing the email as well looks up again with both.
    both = next_lookup(by_phone.key, "91234567", "K.Liao@Gmail.com")
    assert both == ("id:91234567|k.liao@gmail.com", "91234567", "k.liao@gmail.com")
    # Malformed phones / emails never trigger.
    assert next_lookup(None, "9123", "katherine at example") is None
    assert next_lookup(None, "", "") is None


def test_email_alone_triggers_a_lookup():
    assert next_lookup(None, "", "katherine.liao@gmail.com") == (
        "id:katherine.liao@gmail.com",
        None,
        "katherine.liao@gmail.com",
    )


@pytest.mark.parametrize("strategy", ["contact_number", "email"])
def test_records_prefill_only_on_phone_or_email_match(strategy):
    customer = {
        "name": "Katherine Liao",
        "contact_number": "+65 9123 4567",
        "email": "katherine.liao@gmail.com",
        "x": 1,
    }
    assert records_prefill({"match_strategy": strategy, "customer": customer}) == {
        "name": "Katherine Liao",
        "contact_number": "+65 9123 4567",
        "email": "katherine.liao@gmail.com",
    }
    assert records_prefill({"match_strategy": "name", "customer": customer}) is None
    assert records_prefill({"status": "not_found"}) is None


def test_cases_key():
    assert cases_key(None, []) == "-|"
    assert cases_key("name", [{"case_id": "A"}, {"case_id": "B"}]) == "name|A,B"
