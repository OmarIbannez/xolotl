"""Bucket choice and window arithmetic for declared aggregates (spec §8.2).

Bucket choice is mechanical: a ``DURATION`` window up to ``P7D`` keeps ``HOUR``
buckets, up to ``P6M`` ``DAY`` buckets; ``MONTH``, ``QUARTER`` and ``ACCUM`` keep
one bucket per period or accumulation window. ``HOUR`` and ``DAY`` buckets start
at UTC boundaries, ``MONTH``, ``QUARTER`` and ``ACCUM`` buckets at Mexico City
ones (spec §5.2); every start is returned in UTC.

Pure: no database, no web framework and no other module (contract
``monitoring-core-is-pure``).
"""

import calendar
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    WindowKind,
    window_hours,
    window_parts,
)
from xolotl.modules.monitoring.enums import BucketKind
from xolotl.platform.policy.durations import add_iso_duration

HOUR_MAX = "P7D"
DAY_MAX = "P6M"
# The platform calendar's zone (xolotl.platform.calendar.MEXICO_CITY), named here
# because the pure core may not import xolotl.platform.calendar; a test pins them.
MEXICO_CITY_ZONE = ZoneInfo("America/Mexico_City")
_MONTHS_PER_QUARTER = 3
_MONTH_KEY = re.compile(r"^(\d{4})-(\d{2})$")
_QUARTER_KEY = re.compile(r"^(\d{4})-Q([1-4])$")


def bucket_kind_for(declaration: AggregateDeclaration) -> BucketKind:
    """Choose the bucket a declaration keeps.

    Returns:
        ``HOUR`` for a duration up to ``P7D``, ``DAY`` up to ``P6M``, else the
        window kind's own bucket.

    Raises:
        ValueError: When a ``DURATION`` window is not a duration up to ``P6M``.

    """
    match declaration.window_kind:
        case WindowKind.MONTH:
            return BucketKind.MONTH
        case WindowKind.QUARTER:
            return BucketKind.QUARTER
        case WindowKind.ACCUM:
            return BucketKind.ACCUM
        case WindowKind.DURATION:
            hours = window_hours(declaration.window)
            if hours is not None and hours <= _hours(HOUR_MAX):
                return BucketKind.HOUR
            if hours is not None and hours <= _hours(DAY_MAX):
                return BucketKind.DAY
            msg = f"{declaration.window!r} is not a window up to {DAY_MAX}"
            raise ValueError(msg)


def _hours(window: str) -> int:
    hours = window_hours(window)
    if hours is None:
        msg = f"{window!r} is not a duration"
        raise ValueError(msg)
    return hours


def _mexico_start(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)


def bucket_start_for(kind: BucketKind, at: datetime) -> datetime:
    """Return the start of the bucket of ``kind`` that ``at`` falls in.

    An ``ACCUM`` bucket starts at the Mexico City day of its first operation.

    Returns:
        The start, in UTC.

    """
    at = at.astimezone(UTC)
    match kind:
        case BucketKind.HOUR:
            return at.replace(minute=0, second=0, microsecond=0)
        case BucketKind.DAY:
            return at.replace(hour=0, minute=0, second=0, microsecond=0)
        case BucketKind.MONTH | BucketKind.QUARTER | BucketKind.ACCUM:
            local = at.astimezone(MEXICO_CITY_ZONE).date()
            if kind is BucketKind.ACCUM:
                return _mexico_start(local)
            month = local.month
            if kind is BucketKind.QUARTER:
                month -= (month - 1) % _MONTHS_PER_QUARTER
            return _mexico_start(date(local.year, month, 1))


def _minus(at: datetime, window: str) -> datetime:
    parts = window_parts(window)
    if parts is None:
        msg = f"{window!r} is not a duration"
        raise ValueError(msg)
    months, days, hours = parts
    year, month = divmod(at.year * 12 + at.month - 1 - months, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    shifted = at.replace(year=year, month=month, day=min(at.day, last))
    return shifted - timedelta(days=days, hours=hours)


def window_bounds(
    declaration: AggregateDeclaration, *, at: datetime
) -> tuple[datetime, datetime]:
    """Return the first and last bucket starts a read at ``at`` sums, both included.

    A ``DURATION`` window spans the buckets from the one holding ``at - window``
    to the one holding ``at``; a ``MONTH``, ``QUARTER`` or ``ACCUM`` window is the
    one bucket holding ``at``.

    Returns:
        ``(first, last)`` bucket starts, in UTC.

    """
    kind = bucket_kind_for(declaration)
    last = bucket_start_for(kind, at)
    if declaration.window_kind is not WindowKind.DURATION:
        return last, last
    return bucket_start_for(kind, _minus(at.astimezone(UTC), declaration.window)), last


def period_bucket_start(kind: BucketKind, period_key: str) -> datetime:
    """Return the start of the calendar bucket a period key names.

    Returns:
        The Mexico City start of ``"2027-02"`` (``MONTH``) or ``"2027-Q1"``
        (``QUARTER``), in UTC.

    Raises:
        ValueError: When ``period_key`` is not a key of ``kind``.

    """
    pattern = _MONTH_KEY if kind is BucketKind.MONTH else _QUARTER_KEY
    match = (
        pattern.match(period_key)
        if kind in {BucketKind.MONTH, BucketKind.QUARTER}
        else None
    )
    if match is None:
        msg = f"{period_key!r} is not a {kind} period key"
        raise ValueError(msg)
    year, number = int(match.group(1)), int(match.group(2))
    month = (
        number if kind is BucketKind.MONTH else (number - 1) * _MONTHS_PER_QUARTER + 1
    )
    return _mexico_start(date(year, month, 1))


def accum_window(first_at: datetime, window: str) -> tuple[datetime, datetime]:
    """Return the accumulation window a key's first qualifying operation opens.

    Returns:
        The Mexico City day start of ``first_at`` and that day plus ``window``,
        both in UTC.

    """
    day = first_at.astimezone(MEXICO_CITY_ZONE).date()
    return _mexico_start(day), _mexico_start(add_iso_duration(day, window))
