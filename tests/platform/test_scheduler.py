"""Cron schedules: slot arithmetic, the upsert and the scheduler tick (spec §9)."""

import logging
import threading
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import override
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from pydantic import JsonValue
from sqlalchemy import Engine, create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import make_tenant, system_ctx
from tests.platform.jobs_support import (
    T0,
    FakeClock,
    enqueue,
    fetch_jobs,
    fetch_schedule,
)
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus
from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.jobs import scheduler as scheduler_module
from xolotl.platform.jobs.models import JobSchedule
from xolotl.platform.jobs.queue import PostgresJobQueue, next_slot
from xolotl.platform.jobs.scheduler import Scheduler

MEXICO_CITY = "America/Mexico_City"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def queue(engine: Engine, clock: FakeClock) -> PostgresJobQueue:
    return PostgresJobQueue(get_sessionmaker(), clock_fn=clock)


@pytest.fixture
def scheduler(queue: PostgresJobQueue, clock: FakeClock) -> Scheduler:
    return Scheduler(queue, get_sessionmaker(), clock_fn=clock)


def add_schedule(
    queue: PostgresJobQueue,
    tenant_id: UUID,
    *,
    name: str = "tick",
    cron: str = "*/5 * * * *",
    tz: str = "UTC",
    kind: str = "demo.tick",
    payload: dict[str, JsonValue] | None = None,
) -> UUID:
    with tenant_session(system_ctx(tenant_id)) as s:
        return queue.schedule(
            s,
            name=name,
            cron=cron,
            tz=tz,
            kind=kind,
            payload=payload if payload is not None else {},
        )


def set_enabled(tenant_id: UUID, name: str, *, enabled: bool) -> None:
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            update(JobSchedule).where(JobSchedule.name == name).values(enabled=enabled)
        )


def corrupt(tenant_id: UUID, name: str, **values) -> None:
    """Edit a schedule row directly, as a bad migration or a hand edit would."""
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            update(JobSchedule).where(JobSchedule.name == name).values(**values)
        )


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 19, hour, minute, second, tzinfo=UTC)


# --- slot arithmetic ---------------------------------------------------------------


def test_next_slot_is_the_first_match_strictly_after_the_instant():
    assert next_slot("*/5 * * * *", "UTC", after=at(12, 0)) == at(12, 5)
    assert next_slot("*/5 * * * *", "UTC", after=at(12, 4, 59)) == at(12, 5)
    assert next_slot("*/5 * * * *", "UTC", after=at(12, 5)) == at(12, 10)


@pytest.mark.parametrize("month", [1, 7, 10])
def test_midnight_in_mexico_city_is_six_in_the_morning_utc_all_year(month: int):
    # Mexico abolished daylight saving time in 2022: the offset is -6 in every month.
    slot = next_slot(
        "0 0 * * *", MEXICO_CITY, after=datetime(2026, month, 15, 12, 0, tzinfo=UTC)
    )
    assert slot == datetime(2026, month, 16, 6, 0, tzinfo=UTC)


def test_wall_clock_slots_follow_daylight_saving_time():
    # New York springs forward on 2026-03-08: noon is 17:00 UTC before, 16:00 after.
    before = next_slot(
        "0 12 * * *", "America/New_York", after=datetime(2026, 3, 7, 12, tzinfo=UTC)
    )
    after = next_slot(
        "0 12 * * *", "America/New_York", after=datetime(2026, 3, 8, 18, tzinfo=UTC)
    )
    assert before == datetime(2026, 3, 7, 17, 0, tzinfo=UTC)
    assert after == datetime(2026, 3, 9, 16, 0, tzinfo=UTC)


def test_a_slot_in_the_repeated_hour_matches_both_occurrences():
    # New York falls back on 2026-11-01: 01:30 happens at 05:30 UTC and at 06:30 UTC.
    first = next_slot(
        "30 1 * * *", "America/New_York", after=datetime(2026, 10, 31, 12, tzinfo=UTC)
    )
    second = next_slot("30 1 * * *", "America/New_York", after=first)
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert second == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)


def test_a_slot_in_the_skipped_hour_matches_when_the_gap_ends():
    # New York springs forward on 2026-03-08: 02:30 does not exist; 03:00 EDT does.
    slot = next_slot(
        "30 2 * * *", "America/New_York", after=datetime(2026, 3, 7, 12, tzinfo=UTC)
    )
    assert slot == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)


def test_next_slot_accepts_the_standard_aliases():
    assert next_slot("@hourly", "UTC", after=at(12, 30)) == at(13, 0)


@pytest.mark.parametrize(
    "cron",
    [
        "",
        "not a cron",
        "* * * *",  # four fields
        "*/5 * * * * *",  # six: a seconds field would fire every second
        "0 0 * * * 2026",  # six: a year field
        "60 * * * *",  # out of range
        "@random",
        "0 0 31 2 *",  # well formed, but there is no 31 February
        "@reboot",
    ],
)
def test_next_slot_rejects_what_cannot_be_a_five_field_expression_that_fires(
    cron: str,
):
    with pytest.raises(ValidationFailedError):
        _ = next_slot(cron, "UTC", after=T0)


@pytest.mark.parametrize(
    "cron",
    [
        "R * * * *",
        "r * * * *",
        "R/15 * * * *",
        "R(0-29) * * * *",
        "R(0-29)/10 * * * *",
        "0 R * * *",
        "0 0 * * R",
        "H * * * *",
        "H(0-29)/10 * * * *",
    ],
)
def test_next_slot_refuses_random_and_hashed_fields(cron: str):
    # croniter draws R at random on every call: the cadence would change at each
    # advance, so a schedule cannot be built on it.
    with pytest.raises(ValidationFailedError, match="same every time"):
        _ = next_slot(cron, "UTC", after=T0)


def test_weekday_and_month_names_are_not_mistaken_for_random_fields():
    # "fri" and "thu" contain an r and an h.
    assert next_slot("0 9 * * fri", "UTC", after=at(12, 0)) == datetime(
        2026, 10, 23, 9, 0, tzinfo=UTC
    )
    assert next_slot("0 9 * * thu", "UTC", after=at(12, 0)) == datetime(
        2026, 10, 22, 9, 0, tzinfo=UTC
    )
    assert next_slot("0 0 1 mar,jun *", "UTC", after=at(12, 0)) == datetime(
        2027, 3, 1, 0, 0, tzinfo=UTC
    )


def test_next_slot_refuses_a_naive_instant():
    with pytest.raises(ValueError, match="after must be timezone-aware"):
        _ = next_slot("*/5 * * * *", "UTC", after=T0.replace(tzinfo=None))


@pytest.mark.parametrize("tz", ["", "Mars/Olympus", "../etc/passwd", "America"])
def test_next_slot_rejects_unknown_time_zones(tz: str):
    with pytest.raises(ValidationFailedError, match="time zone"):
        _ = next_slot("0 0 * * *", tz, after=T0)


# --- PostgresJobQueue.schedule -----------------------------------------------------


def test_schedule_computes_its_first_slot_at_local_midnight(
    queue: PostgresJobQueue, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    clock.now = at(12, 0)  # 06:00 in Mexico City
    schedule_id = add_schedule(
        queue,
        tenant_id,
        name="nightly",
        cron="0 0 * * *",
        tz=MEXICO_CITY,
        kind="demo.nightly",
        payload={"scope": "all"},
    )
    row = fetch_schedule(tenant_id, "nightly")
    assert row.id == schedule_id
    assert row.next_run_at == datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
    local = row.next_run_at.astimezone(ZoneInfo(MEXICO_CITY))
    assert (local.hour, local.minute, local.utcoffset()) == (
        0,
        0,
        timedelta(hours=-6),
    )
    assert (row.cron, row.tz, row.kind, row.payload) == (
        "0 0 * * *",
        MEXICO_CITY,
        "demo.nightly",
        {"scope": "all"},
    )
    assert (row.enabled, row.last_run_at) == (True, None)


@pytest.mark.parametrize(
    ("cron", "tz"),
    [
        ("not a cron", "UTC"),
        ("0 0 * * *", "Mars/Olympus"),
        ("0 0 31 2 *", "UTC"),
        ("R * * * *", "UTC"),
    ],
)
def test_schedule_rejects_a_bad_expression_or_zone_and_stores_nothing(
    queue: PostgresJobQueue, cron: str, tz: str
):
    tenant_id = make_tenant("alpha")
    with pytest.raises(ValidationFailedError):
        _ = add_schedule(queue, tenant_id, cron=cron, tz=tz)
    with tenant_session(system_ctx(tenant_id)) as s:
        assert s.scalars(select(JobSchedule)).all() == []


def test_schedule_refuses_a_clock_that_returns_naive_datetimes(engine: Engine):
    naive_queue = PostgresJobQueue(
        get_sessionmaker(), clock_fn=lambda: T0.replace(tzinfo=None)
    )
    tenant_id = make_tenant("alpha")
    with pytest.raises(ValueError, match="after must be timezone-aware"):
        _ = add_schedule(naive_queue, tenant_id)


def test_schedule_is_an_upsert_by_name(queue: PostgresJobQueue, clock: FakeClock):
    tenant_id = make_tenant("alpha")
    hourly = {"name": "report", "cron": "0 * * * *"}
    first_id = add_schedule(queue, tenant_id, **hourly, kind="demo.a", payload={"v": 1})
    first_slot = fetch_schedule(tenant_id, "report").next_run_at
    assert first_slot == at(13, 0)

    # 13:10: the 13:00 slot is due but no tick has run. Declaring the same schedule
    # again changes what it runs, and must not postpone that slot to 14:00.
    _ = clock.advance(timedelta(minutes=70))
    again = add_schedule(queue, tenant_id, **hourly, kind="demo.b", payload={"v": 2})
    assert again == first_id
    row = fetch_schedule(tenant_id, "report")
    assert (row.kind, row.payload, row.next_run_at) == ("demo.b", {"v": 2}, first_slot)

    # A different expression recomputes the slot from now.
    changed = add_schedule(
        queue, tenant_id, name="report", cron="30 * * * *", kind="demo.b"
    )
    assert changed == first_id
    row = fetch_schedule(tenant_id, "report")
    assert (row.cron, row.next_run_at) == ("30 * * * *", at(13, 30))


def test_declaring_a_schedule_again_keeps_it_disabled_and_keeps_its_history(
    queue: PostgresJobQueue, scheduler: Scheduler, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    _ = add_schedule(queue, tenant_id)
    _ = clock.advance(timedelta(minutes=6))
    assert scheduler.tick() == 1
    ran_at = fetch_schedule(tenant_id, "tick").last_run_at
    assert ran_at == at(12, 5)
    set_enabled(tenant_id, "tick", enabled=False)

    _ = add_schedule(queue, tenant_id)
    row = fetch_schedule(tenant_id, "tick")
    assert (row.enabled, row.last_run_at) == (False, ran_at)


def test_two_tenants_may_use_the_same_schedule_name(queue: PostgresJobQueue):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    in_alpha = add_schedule(queue, alpha)
    in_beta = add_schedule(queue, beta)
    assert in_alpha != in_beta
    assert fetch_schedule(alpha, "tick").id == in_alpha
    assert fetch_schedule(beta, "tick").id == in_beta


# --- Scheduler.tick ------------------------------------------------------------------


def test_with_the_default_clock_the_first_slot_lies_ahead_of_now(engine: Engine):
    queue = PostgresJobQueue(get_sessionmaker())
    tenant_id = make_tenant("alpha")
    before = datetime.now(UTC)
    _ = add_schedule(queue, tenant_id)
    after = datetime.now(UTC)
    slot = fetch_schedule(tenant_id, "tick").next_run_at
    assert before < slot <= after + timedelta(minutes=5)
    assert slot.minute % 5 == 0
    # Not due yet, judged by the instant before the schedule was created. Reading the
    # wall clock here would make a five-minute boundary that falls between
    # add_schedule and tick() turn the slot due.
    assert Scheduler(queue, get_sessionmaker(), clock_fn=lambda: before).tick() == 0


def test_a_five_minute_schedule_enqueues_once_per_slot(
    queue: PostgresJobQueue, scheduler: Scheduler, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    _ = add_schedule(queue, tenant_id, payload={"scope": "all"})
    assert scheduler.tick() == 0  # 12:00, the first slot is 12:05

    _ = clock.advance(timedelta(minutes=5, seconds=10))
    assert scheduler.tick() == 1
    assert scheduler.tick() == 0  # same slot: nothing is due any more
    (job,) = fetch_jobs(tenant_id, kind="demo.tick")
    assert job.key == "tick:2026-10-19T12:05:00+00:00"
    assert job.payload == {"scope": "all"}
    assert job.status is JobStatus.PENDING
    row = fetch_schedule(tenant_id, "tick")
    assert (row.last_run_at, row.next_run_at) == (at(12, 5), at(12, 10))

    _ = clock.advance(timedelta(minutes=5))
    assert scheduler.tick() == 1
    keys = [j.key for j in fetch_jobs(tenant_id, kind="demo.tick")]
    assert keys == ["tick:2026-10-19T12:05:00+00:00", "tick:2026-10-19T12:10:00+00:00"]


def test_after_three_missed_slots_it_enqueues_once_and_skips_ahead(
    queue: PostgresJobQueue, scheduler: Scheduler, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    _ = add_schedule(queue, tenant_id)
    # The worker was down: 12:05, 12:10 and 12:15 all went by.
    _ = clock.advance(timedelta(minutes=18, seconds=30))

    assert scheduler.tick() == 1
    (job,) = fetch_jobs(tenant_id, kind="demo.tick")
    assert job.key == "tick:2026-10-19T12:05:00+00:00"  # the earliest due slot
    row = fetch_schedule(tenant_id, "tick")
    assert row.last_run_at == at(12, 5)
    assert row.next_run_at == at(12, 20)
    assert row.next_run_at > clock.now
    assert scheduler.tick() == 0  # no backlog is replayed

    _ = clock.advance(timedelta(minutes=2))  # 12:20:30
    assert scheduler.tick() == 1
    assert len(fetch_jobs(tenant_id, kind="demo.tick")) == 2


@pytest.mark.parametrize(
    ("month", "days"),
    [
        (4, range(3, 10)),  # the first Sunday of April, 5th, was the old spring-forward
        (10, range(23, 30)),  # the last Sunday of October, 25th, was the old fall-back
    ],
)
def test_a_daily_mexico_city_schedule_fires_once_a_day_across_the_old_dst_dates(
    queue: PostgresJobQueue,
    scheduler: Scheduler,
    clock: FakeClock,
    month: int,
    days: range,
):
    # Mexico stopped changing its clocks in 2022: local midnight stays 06:00 UTC.
    tenant_id = make_tenant("alpha")
    clock.now = datetime(2026, month, days[0] - 1, 12, 0, tzinfo=UTC)
    _ = add_schedule(
        queue, tenant_id, name="nightly", cron="0 0 * * *", tz=MEXICO_CITY, kind="d.n"
    )
    for day in days:
        clock.now = datetime(2026, month, day, 6, 30, tzinfo=UTC)  # after the slot
        assert scheduler.tick() == 1
        assert scheduler.tick() == 0
    keys = [j.key for j in fetch_jobs(tenant_id, kind="d.n")]
    assert keys == [f"nightly:2026-{month:02d}-{d:02d}T06:00:00+00:00" for d in days]


def test_slot_keys_do_not_depend_on_the_database_session_time_zone(
    queue: PostgresJobQueue, clock: FakeClock
):
    # psycopg returns timestamptz values in the session's zone; a key built from
    # them as they come would change with the server's configuration.
    tenant_id = make_tenant("alpha")
    _ = add_schedule(queue, tenant_id)
    _ = clock.advance(timedelta(minutes=6))
    mexico = create_engine(
        get_settings().database_url,
        connect_args={"options": "-c TimeZone=America/Mexico_City"},
    )
    try:
        factory = sessionmaker(bind=mexico, expire_on_commit=False)
        local = Scheduler(
            PostgresJobQueue(factory, clock_fn=clock), factory, clock_fn=clock
        )
        assert local.tick() == 1
    finally:
        mexico.dispose()
    (job,) = fetch_jobs(tenant_id, kind="demo.tick")
    assert job.key == "tick:2026-10-19T12:05:00+00:00"


def test_a_slot_that_already_has_a_job_is_not_enqueued_twice(
    queue: PostgresJobQueue, scheduler: Scheduler, clock: FakeClock
):
    # What a tick that died after enqueueing but before advancing would have left.
    tenant_id = make_tenant("alpha")
    _ = add_schedule(queue, tenant_id)
    existing = enqueue(
        queue, tenant_id, kind="demo.tick", key="tick:2026-10-19T12:05:00+00:00"
    )
    _ = clock.advance(timedelta(minutes=5, seconds=10))

    assert scheduler.tick() == 0  # the dedup key turned the second enqueue away
    assert [j.id for j in fetch_jobs(tenant_id, kind="demo.tick")] == [existing]
    assert fetch_schedule(tenant_id, "tick").next_run_at == at(12, 10)


class CrashAfterEnqueueQueue(PostgresJobQueue):
    """A queue whose process dies right after it inserts a job."""

    @override
    def enqueue(
        self,
        session: Session,
        *,
        kind: str,
        payload: dict[str, JsonValue],
        run_after: datetime | None = None,
        key: str | None = None,
        max_attempts: int = 8,
    ) -> UUID | None:
        _ = super().enqueue(
            session,
            kind=kind,
            payload=payload,
            run_after=run_after,
            key=key,
            max_attempts=max_attempts,
        )
        msg = "died between the enqueue and the advance"
        raise RuntimeError(msg)


def test_the_enqueue_and_the_advance_commit_together(
    queue: PostgresJobQueue, scheduler: Scheduler, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    _ = add_schedule(queue, tenant_id)
    _ = clock.advance(timedelta(minutes=5, seconds=10))

    crashing = CrashAfterEnqueueQueue(get_sessionmaker(), clock_fn=clock)
    dying = Scheduler(crashing, get_sessionmaker(), clock_fn=clock)
    with pytest.raises(RuntimeError, match="died between"):
        _ = dying.tick()
    assert fetch_jobs(tenant_id, kind="demo.tick") == []  # the insert did not survive
    assert fetch_schedule(tenant_id, "tick").next_run_at == at(12, 5)  # nor the slot

    assert scheduler.tick() == 1  # the next tick picks the slot up, once
    assert len(fetch_jobs(tenant_id, kind="demo.tick")) == 1


def test_a_schedule_with_a_corrupt_expression_does_not_stall_the_others(
    queue: PostgresJobQueue,
    scheduler: Scheduler,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    _ = add_schedule(queue, alpha, name="broken")
    _ = add_schedule(queue, alpha, name="fine")
    _ = add_schedule(queue, beta, name="fine")
    corrupt(alpha, "broken", cron="not a cron")
    _ = clock.advance(timedelta(minutes=6))
    caplog.set_level(logging.WARNING)

    assert scheduler.tick() == 2
    assert [j.key for j in fetch_jobs(alpha)] == ["fine:2026-10-19T12:05:00+00:00"]
    assert [j.key for j in fetch_jobs(beta)] == ["fine:2026-10-19T12:05:00+00:00"]
    broken = fetch_schedule(alpha, "broken")
    assert (broken.next_run_at, broken.last_run_at) == (at(12, 5), None)
    (record,) = [r for r in caplog.records if r.name == scheduler_module.__name__]
    assert record.getMessage() == (
        "schedule skipped: its cron expression or time zone is invalid"
    )
    assert record.levelno == logging.WARNING
    assert record.exc_info is None  # a warning, not a stack trace
    extra = vars(record)  # the fields the scheduler attached to the record
    assert (extra["schedule"], extra["tenant_id"]) == ("broken", str(alpha))


def test_a_corrupt_schedule_is_reported_once_per_definition_not_every_tick(
    queue: PostgresJobQueue,
    scheduler: Scheduler,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
):
    tenant_id = make_tenant("alpha")
    _ = add_schedule(queue, tenant_id, name="broken")
    corrupt(tenant_id, "broken", cron="not a cron")
    _ = clock.advance(timedelta(minutes=6))
    caplog.set_level(logging.WARNING)

    def reports() -> int:
        return len([r for r in caplog.records if r.name == scheduler_module.__name__])

    for _tick in range(3):
        assert scheduler.tick() == 0  # still due, still skipped
    assert reports() == 1  # three ticks, one report

    corrupt(tenant_id, "broken", cron="still not a cron")
    assert scheduler.tick() == 0
    assert reports() == 2  # a new definition is reported again

    corrupt(tenant_id, "broken", cron="*/5 * * * *")  # repaired: it runs ...
    assert scheduler.tick() == 1
    # ... and breaks again, in the very way last reported: that is news again.
    corrupt(tenant_id, "broken", cron="still not a cron")
    _ = clock.advance(timedelta(minutes=6))
    assert scheduler.tick() == 0
    assert reports() == 3


def test_a_disabled_schedule_is_skipped(
    queue: PostgresJobQueue, scheduler: Scheduler, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    _ = add_schedule(queue, tenant_id)
    set_enabled(tenant_id, "tick", enabled=False)
    _ = clock.advance(timedelta(minutes=6))

    assert scheduler.tick() == 0
    assert fetch_jobs(tenant_id) == []
    assert fetch_schedule(tenant_id, "tick").next_run_at == at(12, 5)

    set_enabled(tenant_id, "tick", enabled=True)  # due since 12:05: once, then ahead
    assert scheduler.tick() == 1
    assert fetch_schedule(tenant_id, "tick").next_run_at == at(12, 10)


def test_each_tenants_schedules_enqueue_into_its_own_scope(
    queue: PostgresJobQueue, scheduler: Scheduler, clock: FakeClock
):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    _ = add_schedule(queue, alpha, payload={"who": "alpha"})
    _ = add_schedule(queue, beta, payload={"who": "beta"})
    _ = clock.advance(timedelta(minutes=6))

    assert scheduler.tick() == 2
    assert [j.payload for j in fetch_jobs(alpha)] == [{"who": "alpha"}]
    assert [j.payload for j in fetch_jobs(beta)] == [{"who": "beta"}]


def test_schedulers_ticking_together_enqueue_each_slot_once(
    queue: PostgresJobQueue, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    for n in range(10):
        _ = add_schedule(queue, tenant_id, name=f"s-{n}")
    _ = clock.advance(timedelta(minutes=6))
    schedulers = [
        Scheduler(queue, get_sessionmaker(), clock_fn=clock) for _ in range(2)
    ]
    start = threading.Barrier(2)

    def tick(scheduler: Scheduler) -> int:
        _ = start.wait(timeout=10)
        return scheduler.tick()

    with ThreadPoolExecutor(max_workers=2) as pool:
        enqueued = sum(pool.map(tick, schedulers))
    assert enqueued == 10
    keys = [j.key for j in fetch_jobs(tenant_id)]
    assert len(keys) == len(set(keys)) == 10


def test_tzdata_is_a_runtime_dependency():
    # zoneinfo reads the operating system's IANA database, which slim, distroless and
    # Windows images may not ship; the tzdata package is the fallback.
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()
    )
    dependencies = pyproject["project"]["dependencies"]
    assert any(dependency.lower().startswith("tzdata") for dependency in dependencies)
