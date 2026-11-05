"""Reading a string value from half-streamed JSON (backend.json_stream)."""

from __future__ import annotations

import json

from backend.json_stream import partial_string_value

FULL = json.dumps({"suggestions": [{"possibleConversation": 'Say "hi"\nthen ask é', "topic": "x"}]})


def test_value_grows_with_the_stream():
    seen = [partial_string_value(FULL[:n], "possibleConversation") for n in range(len(FULL) + 1)]
    values = [v for v in seen if v is not None]
    assert values[-1] == 'Say "hi"\nthen ask é'
    assert all(b.startswith(a) for a, b in zip(values, values[1:], strict=False))


def test_nothing_before_the_opening_quote():
    assert partial_string_value('{"suggestions": [{"possibleConversation"', "possibleConversation") is None
    assert partial_string_value('{"possibleConversation": ', "possibleConversation") is None
    assert partial_string_value('{"possibleConversation": "', "possibleConversation") == ""


def test_half_written_escapes_are_left_out():
    assert partial_string_value('{"possibleConversation": "a\\', "possibleConversation") == "a"
    assert partial_string_value('{"possibleConversation": "a\\u00', "possibleConversation") == "a"
    assert partial_string_value('{"possibleConversation": "a\\u00e9b', "possibleConversation") == "aéb"


def test_other_keys_are_ignored():
    text = '{"topic": "Ask about pay", "possibleConversation": "When were'
    assert partial_string_value(text, "possibleConversation") == "When were"
    assert partial_string_value(text, "missing") is None
