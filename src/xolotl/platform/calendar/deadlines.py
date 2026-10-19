"""The deadline DSL: a structured ``DeadlineRule`` and ``due_at`` (spec §7).

A rule is ``{kind, params, tz}``. ``due_at`` turns an aware anchor into the local
date of ``tz`` first, so an anchor given in UTC just after midnight counts from the
Mexico City day it falls on. Day-granular kinds end at 23:59:59 local unless the
rule gives ``params.time``.
"""

import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import ClassVar, Self, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, JsonValue, field_validator, model_validator

from xolotl.platform.calendar.business import (
    add_business_days,
    is_business_day,
    next_business_day,
    nth_business_day_of_month,
    prev_business_day,
)
from xolotl.platform.calendar.holidays import Holidays
from xolotl.platform.errors import ValidationFailedError

MEXICO_CITY = "America/Mexico_City"
END_OF_DAY = time(23, 59, 59)

_MONTH_DAY = re.compile(r"^(\d{2})-(\d{2})$")
_HOUR_MINUTE = re.compile(r"^(\d{2}):(\d{2})$")
# Month-day strings are checked against a common year: a range that ends on
# 29 February has no date in three years out of four.
_COMMON_YEAR = 2001
_ROLLS = frozenset({"NEXT_BUSINESS_DAY", "PREV_BUSINESS_DAY", "NONE"})


class DeadlineKind(StrEnum):
    BUSINESS_DAYS_AFTER = "BUSINESS_DAYS_AFTER"
    NTH_BUSINESS_DAY_OF = "NTH_BUSINESS_DAY_OF"
    DAY_OF_NEXT_MONTH = "DAY_OF_NEXT_MONTH"
    CALENDAR_DAYS_AFTER = "CALENDAR_DAYS_AFTER"
    HOURS_AFTER = "HOURS_AFTER"
    DATE_RANGE = "DATE_RANGE"
    FIXED = "FIXED"


def _integer(
    low: int | None = None, high: int | None = None
) -> Callable[[object], str | None]:
    def check(value: object) -> str | None:
        # bool is an int subclass; true is not a count of days.
        if not isinstance(value, int) or isinstance(value, bool):
            return "must be an integer"
        if (low is not None and value < low) or (high is not None and value > high):
            return (
                f"must be between {low} and {high}"
                if high is not None
                else f"must be ≥ {low}"
            )
        return None

    return check


def _month_day(value: object) -> str | None:
    match = _MONTH_DAY.match(value) if isinstance(value, str) else None
    try:
        _ = date(_COMMON_YEAR, int(match[1]), int(match[2])) if match else None
    except ValueError:
        match = None
    return None if match else "must be a month and day as MM-DD"


def _hour_minute(value: object) -> str | None:
    match = _HOUR_MINUTE.match(value) if isinstance(value, str) else None
    try:
        _ = time(int(match[1]), int(match[2])) if match else None
    except ValueError:
        match = None
    return None if match else "must be a time as HH:MM"


def _roll(value: object) -> str | None:
    return None if value in _ROLLS else f"must be one of {sorted(_ROLLS)}"


def _aware_datetime(value: object) -> str | None:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.utcoffset() is None:
        return "must be an ISO-8601 datetime with an offset"
    return None


_Check = Callable[[object], str | None]
_TIME: dict[str, _Check] = {"time": _hour_minute}
# Per kind: the required keys, then the optional ones, each with its check.
_PARAMS: dict[DeadlineKind, tuple[dict[str, _Check], dict[str, _Check]]] = {
    DeadlineKind.BUSINESS_DAYS_AFTER: ({"n": _integer(1)}, _TIME),
    DeadlineKind.NTH_BUSINESS_DAY_OF: (
        {"month_offset": _integer(), "n": _integer(1)},
        _TIME,
    ),
    DeadlineKind.DAY_OF_NEXT_MONTH: ({"day": _integer(1, 28), "roll": _roll}, _TIME),
    DeadlineKind.CALENDAR_DAYS_AFTER: ({"n": _integer(1)}, _TIME),
    DeadlineKind.HOURS_AFTER: ({"hours": _integer(1)}, {}),
    DeadlineKind.DATE_RANGE: (
        {"start": _month_day, "end": _month_day, "year_offset": _integer()},
        _TIME,
    ),
    DeadlineKind.FIXED: ({"at": _aware_datetime}, {}),
}


class DeadlineRule(BaseModel):
    """When something is due, relative to an anchor; ``params`` depend on ``kind``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    kind: DeadlineKind
    params: dict[str, JsonValue]
    tz: str = MEXICO_CITY

    @field_validator("tz")
    @classmethod
    def _known_zone(cls, tz: str) -> str:
        # ZoneInfoNotFoundError is a KeyError and a zone directory such as "America"
        # raises IsADirectoryError, an OSError; pydantic would let either escape.
        try:
            _ = ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            msg = f"unknown time zone {tz}"
            raise ValueError(msg) from exc
        return tz

    @model_validator(mode="after")
    def _params_fit_the_kind(self) -> Self:
        required, optional = _PARAMS[self.kind]
        unknown = set(self.params) - set(required) - set(optional)
        if unknown:
            msg = f"{self.kind.value} takes no {', '.join(sorted(unknown))}"
            raise ValueError(msg)
        for key, check in {**required, **optional}.items():
            if key not in self.params:
                if key in required:
                    msg = f"{self.kind.value} needs params.{key}"
                    raise ValueError(msg)
                continue
            if (problem := check(self.params[key])) is not None:
                msg = f"params.{key} {problem}"
                raise ValueError(msg)
        return self


def _int(params: Mapping[str, JsonValue], key: str) -> int:
    return cast("int", params[key])


def _str(params: Mapping[str, JsonValue], key: str) -> str:
    return cast("str", params[key])


def _shift_month(day: date, offset: int) -> tuple[int, int]:
    year, month = divmod(day.year * 12 + day.month - 1 + offset, 12)
    return year, month + 1


def _day_of_next_month(
    params: Mapping[str, JsonValue], day: date, holidays: Holidays
) -> date:
    year, month = _shift_month(day, 1)
    due = date(year, month, _int(params, "day"))
    roll = _str(params, "roll")
    if roll == "NEXT_BUSINESS_DAY":
        return next_business_day(due, holidays=holidays)
    # A business day stays put under either roll; prev_business_day looks strictly
    # before its argument.
    if roll == "PREV_BUSINESS_DAY" and not is_business_day(due, holidays=holidays):
        return prev_business_day(due, holidays=holidays)
    return due


def _due_day(rule: DeadlineRule, day: date, holidays: Holidays) -> date:
    params = rule.params
    match rule.kind:
        case DeadlineKind.BUSINESS_DAYS_AFTER:
            return add_business_days(day, _int(params, "n"), holidays=holidays)
        case DeadlineKind.NTH_BUSINESS_DAY_OF:
            year, month = _shift_month(day, _int(params, "month_offset"))
            return nth_business_day_of_month(
                year, month, _int(params, "n"), holidays=holidays
            )
        case DeadlineKind.DAY_OF_NEXT_MONTH:
            return _day_of_next_month(params, day, holidays)
        case DeadlineKind.CALENDAR_DAYS_AFTER:
            return day + timedelta(days=_int(params, "n"))
        case _:
            # DATE_RANGE: the range's end, year_offset years after the anchor's.
            month, day_of_month = map(int, _str(params, "end").split("-"))
            return date(day.year + _int(params, "year_offset"), month, day_of_month)


def due_at(rule: DeadlineRule, anchor: datetime, *, holidays: Holidays) -> datetime:
    """Compute when ``rule`` falls due for ``anchor``.

    Returns:
        An aware datetime in ``rule.tz``.

    Raises:
        ValueError: When ``anchor`` is naive.
        ValidationFailedError: When ``NTH_BUSINESS_DAY_OF`` asks for a business day
            the month does not have.

    """
    if anchor.utcoffset() is None:
        msg = "the anchor needs a time zone"
        raise ValueError(msg)
    zone = ZoneInfo(rule.tz)
    local = anchor.astimezone(zone)
    if rule.kind is DeadlineKind.HOURS_AFTER:
        return local + timedelta(hours=_int(rule.params, "hours"))
    if rule.kind is DeadlineKind.FIXED:
        return datetime.fromisoformat(_str(rule.params, "at")).astimezone(zone)
    try:
        due_day = _due_day(rule, local.date(), holidays)
    except ValueError as exc:
        # Only nth_business_day_of_month raises here: the params were validated.
        raise ValidationFailedError(str(exc)) from exc
    at = (
        time.fromisoformat(_str(rule.params, "time"))
        if "time" in rule.params
        else END_OF_DAY
    )
    return datetime.combine(due_day, at, tzinfo=zone)
