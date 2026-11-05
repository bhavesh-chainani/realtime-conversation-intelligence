from __future__ import annotations

from backend import config as cfg
from backend import issue_guides

GUIDE = {
    "issue_type": "Unpaid or late salary",
    "applies_when": "Salary not paid.",
    "facts_to_gather": "Months unpaid; still employed?",
    "documents": "Payslips.",
    "route": "File a salary claim with TADM.",
    "deadline": "Within 1 year.",
    "follow_up": "Call back in 7 days.",
}


def test_render_lists_each_guide():
    text = issue_guides.render([GUIDE])
    assert text.startswith("SERVICE GUIDE")
    assert "## Unpaid or late salary (when: Salary not paid.)" in text
    assert "- Route: File a salary claim with TADM." in text
    assert "- Follow-up: Call back in 7 days." in text


def test_without_guides_the_agent_is_told_not_to_name_channels():
    assert issue_guides.render([]) == issue_guides.NO_GUIDE


def test_load_without_a_database_keeps_no_guides(monkeypatch):
    monkeypatch.setattr(cfg, "CUSTOMER_HISTORY_DATABASE_URL", "")
    assert issue_guides.load() == 0
