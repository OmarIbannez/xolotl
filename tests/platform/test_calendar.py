"""The regulatory calendar: the holidays file, business days and deadlines (A3)."""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pydantic
import pytest
import yaml
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import sessionmaker

from xolotl.platform.calendar import (
    MEXICO_CITY,
    DeadlineKind,
    DeadlineRule,
    HolidayRow,
    Holidays,
    add_business_days,
    due_at,
    holidays_from_rows,
    is_business_day,
    load_holidays,
    next_business_day,
    nth_business_day_of_month,
    prev_business_day,
    read_holiday_file,
    seed_holidays,
)
from xolotl.platform.calendar.models import Holiday
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import HolidayScope
from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.tenancy.context import TenantContext

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "tests/golden/calendar"
ROWS = read_holiday_file(REPO / "policy-packs/_holidays/MX.yaml")


def holidays_of(scope: HolidayScope | str) -> Holidays:
    return holidays_from_rows(ROWS, country="MX", scope=HolidayScope(scope))


def _golden(name: str) -> Any:
    return yaml.safe_load((GOLDEN / name).read_text(encoding="utf-8"))


BUSINESS = _golden("business_days.yaml")
DEADLINES = _golden("deadlines.yaml")


def _business_cases() -> list[Any]:
    return [
        pytest.param(helper, case, id=f"{helper}-{i}")
        for helper, cases in BUSINESS.items()
        for i, case in enumerate(cases)
    ]


def test_the_file_covers_the_four_scopes_and_three_years():
    assert {row.scope for row in ROWS} == set(HolidayScope)
    assert {row.day.year for row in ROWS} <= {2025, 2026, 2027}
    keys = [(row.country_code, row.scope, row.day) for row in ROWS]
    assert len(keys) == len(set(keys))
    per_scope = {s: sum(1 for row in ROWS if row.scope is s) for s in HolidayScope}
    assert per_scope == {
        HolidayScope.NATIONAL: 22,
        HolidayScope.CNBV: 28,
        HolidayScope.SAT: 30,
        HolidayScope.BANXICO: 28,
    }
    assert len(ROWS) == 108
    assert sum(row.verify for row in ROWS) == 35
    assert holidays_of("BANXICO").days == holidays_of("CNBV").days
    for row in ROWS:
        published = row.scope in {HolidayScope.CNBV, HolidayScope.BANXICO} or (
            row.scope is HolidayScope.SAT and "RMF" in row.source
        )
        if row.day.year == 2027 and published:
            assert row.verify, row
    assert all(row.country_code == "MX" for row in ROWS)
    assert all(isinstance(row, HolidayRow) and row.name and row.source for row in ROWS)


def test_published_scopes_list_weekdays_only():
    # The CFF art. 12 rows of SAT are statutory and keep their weekend days.
    for row in ROWS:
        if row.scope in {HolidayScope.CNBV, HolidayScope.BANXICO} or (
            row.scope is HolidayScope.SAT and "RMF" in row.source
        ):
            assert row.day.weekday() < 5, row


@pytest.mark.parametrize(("helper", "case"), _business_cases())
def test_business_day_goldens(helper: str, case: dict[str, Any]):
    holidays = holidays_of(case["scope"])
    if helper == "is_business_day":
        got: object = is_business_day(case["day"], holidays=holidays)
    elif helper == "add_business_days":
        got = add_business_days(case["day"], case["n"], holidays=holidays)
    elif helper == "next_business_day":
        got = next_business_day(case["day"], holidays=holidays)
    elif helper == "prev_business_day":
        got = prev_business_day(case["day"], holidays=holidays)
    else:
        assert helper == "nth_business_day_of_month"
        args = (case["year"], case["month"], case["n"])
        if "error" in case:
            assert case["error"] == "ValueError"
            with pytest.raises(ValueError, match="business day"):
                _ = nth_business_day_of_month(*args, holidays=holidays)
            return
        got = nth_business_day_of_month(*args, holidays=holidays)
    assert got == case["expected"]


def test_nth_business_day_refuses_a_zero_n():
    with pytest.raises(ValueError, match="business day"):
        _ = nth_business_day_of_month(2026, 1, 0, holidays=holidays_of("CNBV"))


def test_add_business_days_steps_over_weekends_and_holidays_both_ways():
    cnbv = holidays_of("CNBV")
    assert add_business_days(date(2026, 4, 6), -1, holidays=cnbv) == date(2026, 4, 1)
    assert add_business_days(date(2026, 4, 4), 1, holidays=cnbv) == date(2026, 4, 6)


@pytest.mark.parametrize("case", DEADLINES, ids=[case["id"] for case in DEADLINES])
def test_deadline_goldens(case: dict[str, Any]):
    result = due_at(
        DeadlineRule.model_validate(case["rule"]),
        datetime.fromisoformat(case["anchor"]),
        holidays=holidays_of(case["scope"]),
    )
    assert result == datetime.fromisoformat(case["expected"])
    assert result.utcoffset() == timedelta(hours=-6)


def test_every_deadline_kind_has_a_golden():
    assert {DeadlineKind(case["rule"]["kind"]) for case in DEADLINES} == set(
        DeadlineKind
    )


def test_deadline_rule_defaults_to_mexico_city():
    rule = DeadlineRule(kind=DeadlineKind.CALENDAR_DAYS_AFTER, params={"n": 1})
    assert rule.tz == MEXICO_CITY == "America/Mexico_City"


def test_day_of_next_month_rolls_back_to_the_previous_business_day():
    rule = DeadlineRule.model_validate({
        "kind": "DAY_OF_NEXT_MONTH",
        "params": {"day": 17, "roll": "PREV_BUSINESS_DAY"},
    })
    anchor = datetime.fromisoformat("2026-12-31T00:00:00-06:00")
    assert due_at(rule, anchor, holidays=holidays_of("SAT")) == datetime.fromisoformat(
        "2027-01-15T23:59:59-06:00"
    )
    # A 17th that is a business day stays where it is under either roll.
    anchor = datetime.fromisoformat("2026-01-20T00:00:00-06:00")
    assert due_at(rule, anchor, holidays=holidays_of("SAT")) == datetime.fromisoformat(
        "2026-02-17T23:59:59-06:00"
    )


def test_a_naive_anchor_is_refused():
    rule = DeadlineRule(kind=DeadlineKind.HOURS_AFTER, params={"hours": 24})
    with pytest.raises(ValueError, match="time zone"):
        _ = due_at(
            rule,
            datetime.fromisoformat("2026-04-02T10:00:00"),
            holidays=holidays_of("CNBV"),
        )


@pytest.mark.parametrize(
    "rule",
    [
        {"kind": "BUSINESS_DAYS_AFTER", "params": {}},
        {"kind": "DAY_OF_NEXT_MONTH", "params": {"day": 31, "roll": "NONE"}},
        {"kind": "FIXED", "params": {"at": "2026-11-30"}},
        {"kind": "HOURS_AFTER", "params": {"hours": 24, "time": "10:00"}},
        {
            "kind": "NTH_BUSINESS_DAY_OF",
            "params": {"month_offset": 1, "n": 10},
            "tz": "Mars/Olympus",
        },
        {"kind": "BUSINESS_DAYS_AFTER", "params": {"n": 0}},
        {"kind": "BUSINESS_DAYS_AFTER", "params": {"n": True}},
        {"kind": "CALENDAR_DAYS_AFTER", "params": {"n": 1}, "tz": "America"},
        {"kind": "CALENDAR_DAYS_AFTER", "params": {"n": "3"}},
        {"kind": "CALENDAR_DAYS_AFTER", "params": {"n": 3, "extra": 1}},
        {"kind": "CALENDAR_DAYS_AFTER", "params": {"n": 3, "time": "25:00"}},
        {"kind": "DAY_OF_NEXT_MONTH", "params": {"day": 17, "roll": "SOMETIMES"}},
        {"kind": "DATE_RANGE", "params": {"start": "13-01", "end": "03-31"}},
        {
            "kind": "DATE_RANGE",
            "params": {"start": "01-01", "end": "02-30", "year_offset": 0},
        },
        {"kind": "FIXED", "params": {"at": 20261130}},
        {"kind": "FIXED", "params": {"at": "not a date"}},
        {"kind": "NTH_BUSINESS_DAY_OF", "params": {"month_offset": 1, "n": 10}, "x": 1},
    ],
)
def test_rule_params_are_validated(rule: dict[str, Any]):
    with pytest.raises(pydantic.ValidationError):
        _ = DeadlineRule.model_validate(rule)


def test_nth_beyond_the_month_is_a_422():
    rule = DeadlineRule(
        kind=DeadlineKind.NTH_BUSINESS_DAY_OF, params={"month_offset": 1, "n": 25}
    )
    anchor = datetime.fromisoformat("2026-01-31T12:00:00-06:00")
    with pytest.raises(ValidationFailedError):
        _ = due_at(rule, anchor, holidays=holidays_of("NATIONAL"))


def test_month_offset_crosses_years_both_ways():
    rule = DeadlineRule(
        kind=DeadlineKind.NTH_BUSINESS_DAY_OF, params={"month_offset": -13, "n": 1}
    )
    anchor = datetime.fromisoformat("2027-01-10T12:00:00-06:00")
    assert due_at(rule, anchor, holidays=holidays_of("CNBV")) == datetime.fromisoformat(
        "2025-12-01T23:59:59-06:00"
    )


def test_read_holiday_file_refuses_a_missing_or_malformed_file(tmp_path: Path):
    with pytest.raises(ValidationFailedError, match=r"missing\.yaml"):
        _ = read_holiday_file(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    _ = broken.write_text("country: [MX\n", encoding="utf-8")
    with pytest.raises(ValidationFailedError, match=r"broken\.yaml"):
        _ = read_holiday_file(broken)
    wrong = tmp_path / "wrong.yaml"
    _ = wrong.write_text(
        "country: MX\nscopes:\n  MOON:\n    source: x\n    days: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationFailedError, match=r"wrong\.yaml"):
        _ = read_holiday_file(wrong)
    header = "country: MX\nyears: [2026]\nscopes:\n  CNBV:\n    source: x\n    days:\n"
    day = '      - {day: %s, name: "n", basis: "b"}\n'
    outside = tmp_path / "outside.yaml"
    _ = outside.write_text(header + day % "2027-01-01", encoding="utf-8")
    with pytest.raises(ValidationFailedError, match="outside the years"):
        _ = read_holiday_file(outside)
    twice = tmp_path / "twice.yaml"
    _ = twice.write_text(header + day % "2026-01-01" * 2, encoding="utf-8")
    with pytest.raises(ValidationFailedError, match="listed twice"):
        _ = read_holiday_file(twice)


def test_seed_holidays_is_idempotent_and_load_holidays_reads_the_scope(
    admin_engine: Engine, engine: Engine
):
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    ctx = TenantContext.platform("test:calendar")
    with tenant_session(ctx, factory=owner) as s:
        assert seed_holidays(s, []) == 0
        assert seed_holidays(s, ROWS) == 108
    with tenant_session(ctx, factory=owner) as s:
        assert seed_holidays(s, ROWS) == 108
        assert s.scalar(select(func.count()).select_from(Holiday)) == 108
        cnbv = load_holidays(s, scope=HolidayScope.CNBV)
    assert cnbv.country == "MX"
    assert cnbv.scope is HolidayScope.CNBV
    assert date(2026, 4, 2) in cnbv.days
    assert date(2026, 5, 5) not in cnbv.days
    assert cnbv == holidays_of("CNBV")
    app = sessionmaker(bind=engine, expire_on_commit=False)
    with tenant_session(ctx, factory=app) as s:
        assert load_holidays(s, scope=HolidayScope.CNBV) == cnbv
        assert load_holidays(s) == holidays_of("NATIONAL")
