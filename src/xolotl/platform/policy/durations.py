"""The ISO-8601 durations packs write periods in: years, months and days only.

``iso_duration_days`` compares two durations on one scale (a year is 360 days, a
month 30), so ``P1Y`` and ``P12M`` are equal; ``add_iso_duration`` does calendar
arithmetic for a due date or a retention end.
"""

import calendar
import re
from datetime import date, timedelta
from typing import Annotated

from pydantic import AfterValidator

_DURATION = re.compile(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?$")
_DAYS_PER_YEAR = 360
_DAYS_PER_MONTH = 30


def _parts(text: str) -> tuple[int, int, int] | None:
    match = _DURATION.match(text)
    if match is None or not any(match.groups()):
        return None
    years, months, days = (int(part or 0) for part in match.groups())
    return years, months, days


def _not_a_duration(text: str) -> str:
    return f"{text!r} is not an ISO-8601 duration of years, months and days"


def iso_duration_days(text: str) -> int:
    """Measure a duration on the 360-day scale.

    Returns:
        ``years * 360 + months * 30 + days``.

    Raises:
        ValueError: When ``text`` is not ``P[nY][nM][nD]`` with at least one part.

    """
    parts = _parts(text)
    if parts is None:
        raise ValueError(_not_a_duration(text))
    years, months, days = parts
    return years * _DAYS_PER_YEAR + months * _DAYS_PER_MONTH + days


def add_iso_duration(day: date, text: str) -> date:
    """Add a duration to a date: years and months first, then days.

    A day past the end of the target month is clamped to its last day, so 31
    January plus one month is 28 February and 29 February plus a year is 28
    February.

    Returns:
        The shifted date.

    Raises:
        ValueError: When ``text`` is not a duration ``iso_duration_days`` accepts.

    """
    parts = _parts(text)
    if parts is None:
        raise ValueError(_not_a_duration(text))
    years, months, days = parts
    year, month = divmod(day.year * 12 + day.month - 1 + years * 12 + months, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last)) + timedelta(days=days)


def check_iso_duration(text: str) -> str:
    """Validate ``text`` as a duration.

    Returns:
        ``text`` unchanged.

    """
    _ = iso_duration_days(text)
    return text


type IsoDuration = Annotated[str, AfterValidator(check_iso_duration)]
