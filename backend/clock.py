"""The centre's local date, used for deadlines and follow-up dates (the server itself may run on UTC)."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import config as cfg


def today() -> date:
    return datetime.now(ZoneInfo(cfg.CENTRE_TIMEZONE)).date()


def _spoken(day: date) -> str:
    """e.g. "Monday 5 October (2026-10-05)": the words Staff say, and the date the case file needs."""
    return f"{day:%A} {day.day} {day:%B} ({day.isoformat()})"


def today_label() -> str:
    return _spoken(today())


def calendar_label(days: int = 14) -> str:
    """The next `days` days, spoken, separated by "; ". The agents copy a date from here: left to work out a
    weekday, models use the calendar of a year they were trained on."""
    d = today()
    return "; ".join(_spoken(d + timedelta(days=n)) for n in range(1, days + 1))


_WEEKDAYS = "|".join(calendar.day_name)
_MONTHS = {name: i for i, name in enumerate(calendar.month_name) if name}
_WEEKDAY_DATE = re.compile(rf"\b({_WEEKDAYS})(,?\s+)(\d{{1,2}})(\s+)({'|'.join(_MONTHS)})\b(\s+\d{{4}})?")


def fix_weekdays(text: str, around: date | None = None) -> str:
    """Correct the weekday in "Thursday 19 October" (with or without a year) to the real one: models still slip
    despite the calendar. Without a year, the date nearest `around` (today) is meant."""
    around = around or today()

    def fix(match: re.Match) -> str:
        _, gap, day, space, month, year = match.groups()
        try:
            if year:
                real = date(int(year), _MONTHS[month], int(day))
            else:
                candidates = []
                for y in (around.year - 1, around.year, around.year + 1):
                    try:
                        candidates.append(date(y, _MONTHS[month], int(day)))
                    except ValueError:
                        continue
                if not candidates:
                    return match.group(0)
                real = min(candidates, key=lambda d: abs((d - around).days))
        except ValueError:
            return match.group(0)  # not a real date, e.g. 31 November
        return f"{real:%A}{gap}{day}{space}{month}{year or ''}"

    return _WEEKDAY_DATE.sub(fix, text)
