"""Business-day arithmetic over a ``Holidays`` value (pure functions).

A business day is a Monday to Friday that the scope does not list as a holiday.
"""

from calendar import monthrange
from datetime import date, timedelta

from xolotl.platform.calendar.holidays import Holidays

_DAY = timedelta(days=1)
_SATURDAY = 5


def is_business_day(day: date, *, holidays: Holidays) -> bool:
    """Say whether ``day`` is a weekday the scope keeps open.

    Returns:
        True for a Monday to Friday not in ``holidays``.

    """
    return day.weekday() < _SATURDAY and day not in holidays.days


def next_business_day(day: date, *, holidays: Holidays) -> date:
    """Find the first business day on or after ``day``.

    Returns:
        ``day`` itself when it is a business day, else the next one.

    """
    while not is_business_day(day, holidays=holidays):
        day += _DAY
    return day


def prev_business_day(day: date, *, holidays: Holidays) -> date:
    """Find the last business day strictly before ``day``.

    Returns:
        The business day before ``day``.

    """
    day -= _DAY
    while not is_business_day(day, holidays=holidays):
        day -= _DAY
    return day


def add_business_days(day: date, n: int, *, holidays: Holidays) -> date:
    """Move ``n`` business days from ``day``, backwards when ``n`` is negative.

    Returns:
        ``day`` when ``n`` is 0, else the ``abs(n)``-th business day after (or
        before) it.

    """
    step = _DAY if n > 0 else -_DAY
    remaining = abs(n)
    while remaining:
        day += step
        if is_business_day(day, holidays=holidays):
            remaining -= 1
    return day


def nth_business_day_of_month(
    year: int, month: int, n: int, *, holidays: Holidays
) -> date:
    """Find the ``n``-th business day of a month, counting from 1.

    Returns:
        That day.

    Raises:
        ValueError: When ``n`` is below 1 or the month has fewer business days.

    """
    days = [
        day
        for day in (
            date(year, month, d) for d in range(1, monthrange(year, month)[1] + 1)
        )
        if is_business_day(day, holidays=holidays)
    ]
    if not 1 <= n <= len(days):
        msg = f"{year}-{month:02d} has no business day number {n}"
        raise ValueError(msg)
    return days[n - 1]
