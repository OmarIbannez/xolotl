"""The ``units.fetch`` job: its window, its upserts and its failure notices.

The job fetches UDI and USD_MXN_FIX from the day after the latest stored row (or the
last 31 days of an empty table) to today in Mexico City, upserts what the source
returns, and on a source failure notifies ADMIN and MLRO and completes, so the next
slot tries again.
"""

import itertools
import logging
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import override
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select

from tests.helpers import make_tenant, platform_ctx, system_ctx
from tests.platform.jobs_support import FakeClock, enqueue, fetch_job
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import Confidence, JobStatus
from xolotl.platform.events import InAppNotifier
from xolotl.platform.events.models import Notification
from xolotl.platform.jobs.queue import PostgresJobQueue
from xolotl.platform.jobs.runner import JobRunner
from xolotl.platform.units import (
    UNITS,
    UNITS_FETCH_KIND,
    FetchReport,
    UnitsFetchJob,
    UnitSourceError,
    UnitValue,
    UnitValueRow,
    fetch_missing,
    register_units,
)

# 03:00 UTC on 21 January is still 20 January in Mexico City.
NOW = datetime(2026, 1, 21, 3, 0, tzinfo=UTC)
TODAY = date(2026, 1, 20)


class FakeSource:
    """A ``UnitValueSource`` over a dict that records the windows it serves."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, date], Decimal] = {}
        self.windows: list[tuple[str, date, date]] = []
        self.fail: bool = False

    def _value(self, unit_code: str, day: date) -> UnitValue:
        return UnitValue(
            unit_code=unit_code,
            valid_from=day,
            valid_to=day,
            value=self.values[unit_code, day],
            quote_ccy=UNITS[unit_code].quote_ccy,
            source="fake",
            confidence=Confidence.VERIFIED,
        )

    def fetch(self, unit_code: str, start: date, end: date) -> list[UnitValue]:
        self.windows.append((unit_code, start, end))
        if self.fail:
            msg = "the fake source is down"
            raise UnitSourceError(msg)
        days = sorted(d for (u, d) in self.values if u == unit_code)
        return [self._value(unit_code, d) for d in days if start <= d <= end]

    def latest_available(self, unit_code: str) -> UnitValue | None:
        days = sorted(d for (u, d) in self.values if u == unit_code)
        return self._value(unit_code, days[-1]) if days else None


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def queue(engine: Engine, clock: FakeClock) -> PostgresJobQueue:
    return PostgresJobQueue(get_sessionmaker(), clock_fn=clock)


@pytest.fixture
def fake() -> FakeSource:
    return FakeSource()


@pytest.fixture
def units_job(fake: FakeSource, clock: FakeClock) -> UnitsFetchJob:
    return register_units(source=fake, notifier=InAppNotifier(), clock_fn=clock)


def make_runner(queue: PostgresJobQueue, **options) -> JobRunner:
    return JobRunner(queue, get_sessionmaker(), worker_id="w-1", **options)


def seed_days(fake: FakeSource, unit_code: str, start: date, days: int) -> None:
    for offset in range(days):
        fake.values[unit_code, start + timedelta(days=offset)] = Decimal(
            f"8.{offset + 100}"
        )


def unit_value_count() -> int:
    with tenant_session(platform_ctx()) as s:
        return s.scalar(select(func.count()).select_from(UnitValueRow)) or 0


def notifications(tenant_id: UUID) -> list[tuple[str, str | None, dict[str, str]]]:
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(select(Notification)).all()
        found = [
            (r.template, r.recipient_role, {k: str(v) for k, v in r.data.items()})
            for r in rows
        ]
    return sorted(found, key=repr)


def test_fetch_missing_inserts_and_corrects(engine: Engine, fake: FakeSource):
    seed_days(fake, "UDI", date(2026, 1, 1), 3)
    start, end = date(2026, 1, 1), date(2026, 1, 3)
    with tenant_session(platform_ctx()) as s:
        first = fetch_missing(s, fake, "UDI", start=start, end=end, now=NOW)
    assert first == FetchReport("UDI", start, end, fetched=3, inserted=3, corrected=0)
    with tenant_session(platform_ctx()) as s:
        again = fetch_missing(s, fake, "UDI", start=start, end=end, now=NOW)
    assert (again.fetched, again.inserted, again.corrected) == (3, 0, 0)

    fake.values["UDI", date(2026, 1, 2)] = Decimal("9.000000")
    later = NOW + timedelta(hours=1)
    with tenant_session(platform_ctx()) as s:
        corrected = fetch_missing(s, fake, "UDI", start=start, end=end, now=later)
    assert (corrected.fetched, corrected.inserted, corrected.corrected) == (3, 0, 1)
    assert unit_value_count() == 4


def test_the_job_backfills_31_days_when_the_table_is_empty(
    engine: Engine, units_job: UnitsFetchJob
):
    with tenant_session(platform_ctx()) as s:
        assert units_job.window(s, "UDI") == (date(2025, 12, 20), TODAY)


def test_the_job_continues_from_the_latest_row(
    engine: Engine, units_job: UnitsFetchJob, fake: FakeSource
):
    seed_days(fake, "UDI", date(2026, 1, 13), 3)  # up to 2026-01-15
    with tenant_session(platform_ctx()) as s:
        _ = fetch_missing(
            s, fake, "UDI", start=date(2026, 1, 1), end=date(2026, 1, 31), now=NOW
        )
    with tenant_session(platform_ctx()) as s:
        assert units_job.window(s, "UDI") == (date(2026, 1, 16), TODAY)

    seed_days(fake, "UDI", date(2026, 1, 16), 5)  # up to today
    with tenant_session(platform_ctx()) as s:
        _ = fetch_missing(s, fake, "UDI", start=date(2026, 1, 16), end=TODAY, now=NOW)
    with tenant_session(platform_ctx()) as s:
        assert units_job.window(s, "UDI") is None


def test_the_job_is_idempotent_across_runs(
    engine: Engine,
    units_job: UnitsFetchJob,
    fake: FakeSource,
    queue: PostgresJobQueue,
):
    seed_days(fake, "UDI", date(2026, 1, 1), 15)
    seed_days(fake, "USD_MXN_FIX", date(2026, 1, 1), 15)
    tenant_id = make_tenant("alpha")
    runner = make_runner(queue, kinds=[UNITS_FETCH_KIND])

    first = enqueue(queue, tenant_id, kind=UNITS_FETCH_KIND)
    assert runner.run_once() == 1
    after_first = unit_value_count()
    assert after_first == 30

    second = enqueue(queue, tenant_id, kind=UNITS_FETCH_KIND)
    assert runner.run_once() == 1
    assert unit_value_count() == after_first
    for job_id in (first, second):
        assert fetch_job(tenant_id, job_id).status is JobStatus.DONE
    assert fake.windows == [
        ("UDI", date(2025, 12, 20), TODAY),
        ("USD_MXN_FIX", date(2025, 12, 20), TODAY),
        ("UDI", date(2026, 1, 16), TODAY),
        ("USD_MXN_FIX", date(2026, 1, 16), TODAY),
    ]


def test_a_failing_source_notifies_admin_and_mlro_and_completes(
    engine: Engine,
    units_job: UnitsFetchJob,
    fake: FakeSource,
    queue: PostgresJobQueue,
    caplog: pytest.LogCaptureFixture,
):
    fake.fail = True
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id, kind=UNITS_FETCH_KIND)
    with caplog.at_level(logging.WARNING):
        assert make_runner(queue, kinds=[UNITS_FETCH_KIND]).run_once() == 1

    assert fetch_job(tenant_id, job_id).status is JobStatus.DONE
    window = {"from": "2025-12-20", "to": "2026-01-20"}
    expected = [
        ("units.fetch_failed", role, {"unit": unit, **window})
        for unit in ("UDI", "USD_MXN_FIX")
        for role in ("ADMIN", "MLRO")
    ]
    assert notifications(tenant_id) == sorted(expected, key=repr)
    failures = [r for r in caplog.records if r.getMessage() == "units fetch failed"]
    assert len(failures) == 2
    assert all(r.levelno == logging.WARNING for r in failures)
    assert unit_value_count() == 0


class SlowFakeSource(FakeSource):
    """A fake whose fetches take a moment, so two runs would overlap in them."""

    @override
    def fetch(self, unit_code: str, start: date, end: date) -> list[UnitValue]:
        time.sleep(0.3)
        return super().fetch(unit_code, start, end)


def test_two_tenants_runs_on_a_threaded_runner_take_turns(
    engine: Engine, queue: PostgresJobQueue
):
    slow = SlowFakeSource()
    seed_days(slow, "UDI", date(2025, 12, 20), 32)  # up to today
    seed_days(slow, "USD_MXN_FIX", date(2025, 12, 20), 32)
    ticks = itertools.count()

    def moving_clock() -> datetime:
        # Each run stamps its rows apart, so the unique (unit, day, fetched_at)
        # key cannot hide a second insert of the same day.
        return NOW + timedelta(microseconds=next(ticks))

    _ = register_units(source=slow, notifier=InAppNotifier(), clock_fn=moving_clock)
    for name in ("alpha", "beta"):
        _ = enqueue(queue, make_tenant(name), kind=UNITS_FETCH_KIND)

    runner = make_runner(queue, kinds=[UNITS_FETCH_KIND], threads=2)
    assert runner.run_once() == 2

    with tenant_session(platform_ctx()) as s:
        per_day = s.execute(
            select(UnitValueRow.unit_code, UnitValueRow.valid_from, func.count())
            .where(UnitValueRow.superseded_by.is_(None))
            .group_by(UnitValueRow.unit_code, UnitValueRow.valid_from)
        ).all()
    assert len(per_day) == 64
    assert {count for _, _, count in per_day} == {1}
    # The second run waited for the first and found the store up to date: the
    # source was asked once per unit.
    assert [unit for unit, _, _ in slow.windows] == ["UDI", "USD_MXN_FIX"]
