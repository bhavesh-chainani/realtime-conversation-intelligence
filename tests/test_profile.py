from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.profile import Profile, cases_key, is_nric_shape, next_lookup, records_prefill

PRECEDENCE = json.loads((Path(__file__).parent / "fixtures" / "profile_precedence.json").read_text())


@pytest.mark.parametrize("case", PRECEDENCE, ids=[c["case"] for c in PRECEDENCE])
def test_field_precedence(case):
    """Shared with frontend/app/lib/customer-profile.test.ts so both sides apply the same rules."""
    profile = Profile.from_request(case["values"], case["sources"])
    assert profile.apply(case["patch"], case["source"]) == case["accepted"]


def test_missing_and_suggestion_payload():
    profile = Profile.from_request({"name": "Katherine Liao"}, {"name": "heard"})
    assert profile.missing() == ["nric_worker_permit_id", "address", "purpose_of_call"]
    assert profile.suggestion_payload("name") == {"name": "Katherine Liao", "record_match": "name"}
    assert profile.suggestion_payload(None) == {"name": "Katherine Liao"}


def test_is_nric_shape():
    assert is_nric_shape("s123 4567a")
    assert not is_nric_shape("S88")
    assert not is_nric_shape(None)


def test_lookup_guard_name_first_id_supersedes_no_repeats():
    by_name = next_lookup(None, "Katherine Liao", "")
    assert by_name == ("name:katherine liao", "Katherine Liao", None)
    assert next_lookup(by_name.key, "Katherine Liao", "") is None

    by_id = next_lookup(by_name.key, "Katherine Liao", "s1234567a")
    assert by_id == ("id:S1234567A", None, "S1234567A")
    assert next_lookup(by_id.key, "Katherine Liao", "S1234567A") is None
    # After an ID lookup, a name change alone does not trigger a name lookup.
    assert next_lookup(by_id.key, "Katherine Tan", "") is None
    # Single names and malformed IDs never trigger.
    assert next_lookup(None, "Katherine", "S88") is None


def test_records_prefill_only_on_nric_match():
    customer = {
        "name": "Katherine Liao",
        "nric_worker_permit_id": "S1234567A",
        "address": "12 Tampines",
        "x": 1,
    }
    assert records_prefill({"match_strategy": "nric_worker_permit_id", "customer": customer}) == {
        "name": "Katherine Liao",
        "nric_worker_permit_id": "S1234567A",
        "address": "12 Tampines",
    }
    assert records_prefill({"match_strategy": "name", "customer": customer}) is None
    assert records_prefill({"status": "not_found"}) is None


def test_cases_key():
    assert cases_key(None, []) == "-|"
    assert cases_key("name", [{"case_id": "A"}, {"case_id": "B"}]) == "name|A,B"
