from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from backend.agents.suggestion_agent import suggest_with_fallback
from backend.text_guard import contains_foreign_script, has_foreign_script


def test_has_foreign_script():
    assert has_foreign_script("Katherine Liao called بشأن Brightpath")
    assert has_foreign_script("关于 the deduction")
    # English punctuation, currency, accents and symbols are fine.
    assert not has_foreign_script(
        "Katherine’s $450 deduction — café, naïve · “admin penalty” 2026-03117"
    )
    assert contains_foreign_script({"a": ["fine", {"b": "привет"}]})
    assert not contains_foreign_script({"a": ["fine", 3, None]})


class _SequencedClient:
    """Returns each canned reply in turn, recording how many calls were made."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls = 0

        async def create(**_):
            content = self.replies[min(self.calls, len(self.replies) - 1)]
            self.calls += 1
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def test_foreign_suggestion_is_rejected(monkeypatch):
    reply = json.dumps(
        {
            "should_suggest": True,
            "suggestions": [
                {"topic": "Ask بشأن payslip", "details": {"possibleConversation": "x"}}
            ],
        }
    )
    fake = _SequencedClient(reply)
    monkeypatch.setattr(
        "backend.agents.suggestion_agent.get_async_llm_client", lambda: fake
    )

    body = asyncio.run(
        suggest_with_fallback("Staff: Hi\nCustomer: My salary was cut.", 1)
    )

    assert body["fallback"] is True
