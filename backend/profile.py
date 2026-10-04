"""The caller profile: which value wins for each field, and when to look the caller up.

The frontend applies the same precedence when it receives patches (frontend/app/lib/customer-profile.ts);
tests/fixtures/profile_precedence.json is checked by both test suites so the two cannot drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple

FIELDS = ("name", "nric_worker_permit_id", "address", "purpose_of_call")

# Where a value came from: the instant regex, LLM extraction, the customer DB, or a staff edit.
Source = Literal["heard", "ai", "records", "manual"]

NRIC_SHAPE = re.compile(r"[STFGM]\d{7}[A-Z]")


def is_nric_shape(value: str | None) -> bool:
    return bool(NRIC_SHAPE.fullmatch(re.sub(r"\s+", "", value or "").upper()))


@dataclass
class Profile:
    values: dict[str, str] = field(default_factory=lambda: dict.fromkeys(FIELDS, ""))
    sources: dict[str, Source] = field(default_factory=dict)

    @classmethod
    def from_request(cls, customer: dict[str, str], sources: dict[str, Source]) -> Profile:
        values = {f: (customer.get(f) or "").strip() for f in FIELDS}
        return cls(values, {f: s for f, s in sources.items() if f in FIELDS and values[f]})

    def apply(self, patch: dict[str, str | None], source: Source) -> dict[str, str]:
        """Merge `patch` and return the values that changed.

        Staff edits always win; DB records beat anything heard or extracted; the instant regex hears
        the NRIC exactly, so LLM reformatting may not replace it.
        """
        accepted: dict[str, str] = {}
        for name, raw in patch.items():
            value = (raw or "").strip()
            if name not in FIELDS or not value:
                continue
            current = self.sources.get(name)
            if current == "manual" or (current == "records" and source != "records"):
                continue
            if source == "ai" and name == "nric_worker_permit_id" and current == "heard":
                continue
            if self.values[name] == value and current == source:
                continue
            self.values[name] = value
            self.sources[name] = source
            accepted[name] = value
        return accepted

    def missing(self) -> list[str]:
        return [f for f in FIELDS if not self.values[f]]

    def suggestion_payload(self, record_match: str | None) -> dict[str, str]:
        """Profile fields for the suggestion agent; `record_match` says how the DB record was matched."""
        out = {f: v for f, v in self.values.items() if v}
        if record_match:
            out["record_match"] = record_match
        return out


class LookupRequest(NamedTuple):
    key: str
    name: str | None = None
    nric: str | None = None


def next_lookup(previous_key: str | None, name: str, nric: str) -> LookupRequest | None:
    """The DB lookup to run, or None. A valid ID always wins and supersedes an earlier name lookup;
    a full name (2+ words) is used only until an ID lookup has happened. Each identity is looked up once.
    """
    clean_id = re.sub(r"\s+", "", nric or "").upper()
    if is_nric_shape(clean_id):
        key = f"id:{clean_id}"
        return None if key == previous_key else LookupRequest(key, nric=clean_id)
    if previous_key and previous_key.startswith("id:"):
        return None
    clean_name = " ".join((name or "").split())
    if len(clean_name.split(" ")) >= 2:
        key = f"name:{clean_name.lower()}"
        return None if key == previous_key else LookupRequest(key, name=clean_name)
    return None


def records_prefill(result: dict[str, Any]) -> dict[str, str] | None:
    """Fields to fill from a lookup result. Only an NRIC match is a verified identity."""
    customer = result.get("customer")
    if result.get("match_strategy") != "nric_worker_permit_id" or not isinstance(customer, dict):
        return None
    prefill = {
        f: str(customer[f]).strip()
        for f in ("name", "nric_worker_permit_id", "address")
        if isinstance(customer.get(f), str) and customer[f].strip()
    }
    return prefill or None


def cases_key(match: str | None, cases: list[dict[str, Any]]) -> str:
    """Identifies the customer record a suggestion was based on."""
    return f"{match or '-'}|{','.join(str(c.get('case_id', '')) for c in cases)}"
