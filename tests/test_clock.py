"""The centre's dates as the agents see them (backend.clock)."""

from __future__ import annotations

from datetime import date

from backend import clock


def test_calendar_lists_the_next_days_with_weekdays(monkeypatch):
    monkeypatch.setattr(clock, "today", lambda: date(2026, 10, 13))  # a Tuesday
    days = clock.calendar_label(7).split("; ")
    assert days[0] == "Wednesday 14 October (2026-10-14)" and days[-1] == "Tuesday 20 October (2026-10-20)"
    assert clock.today_label() == "Tuesday 13 October (2026-10-13)"
    assert len(days) == 7


def test_wrong_weekdays_are_corrected():
    around = date(2026, 10, 14)
    assert (
        clock.fix_weekdays("We'll call you on Thursday 19 October.", around)
        == "We'll call you on Monday 19 October."
    )
    assert clock.fix_weekdays("by Friday, 16 October 2026", around) == "by Friday, 16 October 2026"
    assert clock.fix_weekdays("Sunday 17 October and Tuesday 3 November", around) == (
        "Saturday 17 October and Tuesday 3 November"
    )
    # The nearest year is meant: early January seen from December is next year.
    assert clock.fix_weekdays("Monday 4 January", date(2026, 12, 20)) == "Monday 4 January"  # 2027-01-04
    assert (
        clock.fix_weekdays("Monday 31 November, Friday the 13th", around)
        == "Monday 31 November, Friday the 13th"
    )
