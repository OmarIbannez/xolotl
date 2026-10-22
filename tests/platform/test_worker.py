"""The worker loop, its thread inside the API process, and the platform wiring.

A tick hands out due events, runs due jobs, then enqueues due schedules, each step on
its own: one that fails is logged and the others still run. Between ticks the loop
pauses ``WORKER_TICK_SECONDS`` holding no database connection, and ``stop()`` or a
SIGTERM/SIGINT ends it after the tick in progress. ``serve`` runs the loop on a
daemon thread of the API (``XOLOTL_EMBEDDED_WORKER``, on by default; the suite turns
it off and these tests turn it back on), sharing the API's session factory and its
platform services; ``xolotl worker`` runs the same loop in a process of its own.
"""

import logging
import signal
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import timedelta
from types import FrameType
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text, update
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from tests.helpers import first_login, make_tenant, new_tenant, system_ctx
from tests.platform.jobs_support import eventually, unique_kind
from xolotl import worker as worker_module
from xolotl.app import build_app, build_worker, wire_platform
from xolotl.cli import app as cli
from xolotl.modules.monitoring.aggregates.backfill import AggBackfillJob
from xolotl.modules.monitoring.kinds import (
    AGG_BACKFILL_KIND,
    BACKFILL_KIND,
    INGEST_FILE_KIND,
    PARTITIONS_KIND,
    PROCESS_KIND,
)
from xolotl.modules.monitoring.kinds import SWEEP_KIND as MONITORING_SWEEP_KIND
from xolotl.modules.monitoring.process.backfill import BackfillJob
from xolotl.modules.monitoring.process.job import ProcessJob
from xolotl.modules.monitoring.process.partitions import PartitionsJob
from xolotl.modules.monitoring.process.sweep import SweepJob
from xolotl.modules.monitoring.simulation.job import SIMULATE_KIND, handle_simulate
from xolotl.modules.monitoring.txn.files import IngestFileJob
from xolotl.modules.screening.catalog.poll import LISTS_POLL_KIND, ListsPollJob
from xolotl.modules.screening.rescreen.party_events import (
    SCREEN_PARTY_KIND,
    SCREENING_CONSUMER,
    screen_party_job,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.api.idempotency import PURGE_KIND, purge_expired_keys
from xolotl.platform.api.models import IdempotencyKey
from xolotl.platform.config import get_settings
from xolotl.platform.db import (
    RoleBypassError,
    get_engine,
    get_sessionmaker,
    tenant_session,
)
from xolotl.platform.enums import JobStatus, OutboxStatus
from xolotl.platform.events import Event, OutboxEventBus, publish, register_subscriber
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.events.registry import consumers, reset_subscribers
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.models import JobRow, JobSchedule
from xolotl.platform.jobs.queue import (
    Job,
    PostgresJobQueue,
    job_handler,
    register_job_handler,
)
from xolotl.platform.jobs.runner import JobRunner
from xolotl.platform.jobs.scheduler import Scheduler
from xolotl.platform.units import UNITS_FETCH_KIND, UnitsFetchJob
from xolotl.platform.workflows import ADVANCE_KIND, SWEEP_KIND, TIMER_KIND
from xolotl.worker import (
    EMBEDDED_THREAD_NAME,
    STOP_SIGNALS,
    EmbeddedWorker,
    Worker,
)

if TYPE_CHECKING:
    from sqlalchemy.pool import QueuePool

    from xolotl.platform.api.app import AppState

WORKER_EVENT = "test.worker.v1"
PASSWORD = "a-long-enough-password"


@pytest.fixture
def clean_registry() -> Generator[None]:
    reset_subscribers()
    yield
    reset_subscribers()


# The registry is process-wide state: every test starts and ends with it empty.
pytestmark = pytest.mark.usefixtures("clean_registry")


def _settings_from(**env: str) -> Generator[None]:
    with pytest.MonkeyPatch.context() as patched:
        for name, value in env.items():
            patched.setenv(name, value)
        get_settings.cache_clear()
        yield
    get_settings.cache_clear()


@pytest.fixture
def quick_ticks() -> Generator[None]:
    """Build workers that do not pause between ticks from the settings."""
    yield from _settings_from(XOLOTL_WORKER_TICK_SECONDS="0")


@pytest.fixture
def embedded_on() -> Generator[None]:
    yield from _settings_from(
        XOLOTL_EMBEDDED_WORKER="true", XOLOTL_WORKER_TICK_SECONDS="0.02"
    )


@pytest.fixture
def embedded_off() -> Generator[None]:
    yield from _settings_from(XOLOTL_EMBEDDED_WORKER="false")


@dataclass
class Fallback:
    """The SIGTERM/SIGINT handler a test installs before the worker installs its own."""

    caught: list[int] = field(default_factory=list)

    def handler(self, number: int, _frame: FrameType | None) -> None:
        self.caught.append(number)


@pytest.fixture
def fallback() -> Generator[Fallback]:
    # Should the worker fail to take the signals, they land here instead of ending
    # the test run; pytest's own handlers come back afterwards.
    installed = Fallback()
    saved = {number: signal.getsignal(number) for number in STOP_SIGNALS}
    for number in STOP_SIGNALS:
        _ = signal.signal(number, installed.handler)
    yield installed
    for number, handler in saved.items():
        _ = signal.signal(number, signal.SIG_DFL if handler is None else handler)


# ---- helpers --------------------------------------------------------------------


def publish_due(tenant_id: UUID, *, key: str = "k") -> Event:
    """Publish one event and age it past the dispatcher's one-second grace window.

    Returns:
        The event.

    """
    event = Event(
        id=uuid7(),
        type=WORKER_EVENT,
        source="xolotl.tests",
        tenant_id=tenant_id,
        subject=f"test/{key}",
        time=clock.now(),
        key=key,
        data={},
    )
    with tenant_session(system_ctx(tenant_id)) as s:
        publish(s, event)
    make_due(tenant_id)
    return event


def make_due(tenant_id: UUID) -> None:
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )


def outbox_status(event_id: UUID, tenant_id: UUID) -> OutboxStatus:
    with tenant_session(system_ctx(tenant_id)) as s:
        return s.get_one(OutboxRow, (tenant_id, event_id)).status


def enqueue(queue: PostgresJobQueue, tenant_id: UUID, kind: str) -> UUID | None:
    with tenant_session(system_ctx(tenant_id)) as s:
        return queue.enqueue(s, kind=kind, payload={})


def due_schedule(queue: PostgresJobQueue, tenant_id: UUID, kind: str) -> None:
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = queue.schedule(
            s, name=kind, cron="0 * * * *", tz="UTC", kind=kind, payload={}
        )
        _ = s.execute(
            update(JobSchedule)
            .where(JobSchedule.name == kind)
            .values(next_run_at=clock.now() - timedelta(minutes=1))
        )


def job_statuses(tenant_id: UUID, kind: str) -> list[JobStatus]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(s.scalars(select(JobRow.status).where(JobRow.kind == kind)))


def recorder(into: list[str]) -> Callable[[Session, Job], None]:
    def run(_session: Session, job: Job) -> None:
        into.append(job.kind)

    return run


def embedded_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == EMBEDDED_THREAD_NAME]


def loop_parts() -> tuple[OutboxEventBus, JobRunner, Scheduler, PostgresJobQueue]:
    factory = get_sessionmaker()
    queue = PostgresJobQueue(factory)
    return (
        OutboxEventBus(factory),
        JobRunner(queue, factory, worker_id="w-test"),
        Scheduler(queue, factory),
        queue,
    )


# ---- one tick -------------------------------------------------------------------


def test_a_published_ping_reaches_a_subscriber_within_three_ticks(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers: dict[str, str],
    quick_ticks: None,
):
    received: list[Event] = []

    def receive(_session: Session, event: Event) -> None:
        received.append(event)

    register_subscriber("test.ping.v1", receive, consumer="test-ping")
    pong = client.post(
        "/v1/ping", json={}, headers={**admin_headers, "Idempotency-Key": "w1"}
    )
    assert pong.status_code == 200, pong.text
    make_due(tenant.tenant_id)

    assert build_worker().run(max_ticks=3) == 3

    assert [(e.type, e.key) for e in received] == [
        ("test.ping.v1", str(tenant.admin_user_id))
    ]
    (event,) = received
    assert outbox_status(event.id, tenant.tenant_id) is OutboxStatus.PUBLISHED


def test_a_tick_hands_out_events_then_runs_jobs_then_enqueues_schedules(
    engine: Engine, quick_ticks: None
):
    tenant_id = make_tenant("alpha")
    queue = PostgresJobQueue(get_sessionmaker())
    follow_up, scheduled = unique_kind("follow-up"), unique_kind("scheduled")
    ran: list[str] = []

    def queue_a_follow_up(session: Session, _event: Event) -> None:
        _ = queue.enqueue(session, kind=follow_up, payload={})

    register_subscriber(WORKER_EVENT, queue_a_follow_up, consumer="test-order")
    register_job_handler(follow_up, recorder(ran))
    register_job_handler(scheduled, recorder(ran))
    _ = publish_due(tenant_id)
    due_schedule(queue, tenant_id, scheduled)
    worker = build_worker()

    assert worker.run(max_ticks=1) == 1
    # Events went first, so the job their handler queued ran in the same tick; the
    # schedules came last, so the job they queued waits for the next tick.
    assert ran == [follow_up]
    assert job_statuses(tenant_id, scheduled) == [JobStatus.PENDING]
    assert worker.run(max_ticks=1) == 1
    assert ran == [follow_up, scheduled]


@pytest.mark.parametrize("failing", ["dispatch", "jobs", "schedules"])
def test_a_failing_step_is_logged_and_the_others_still_run(
    engine: Engine,
    failing: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant_id = make_tenant("alpha")
    bus, runner, scheduler, queue = loop_parts()
    direct, scheduled = unique_kind("direct"), unique_kind("scheduled")
    received: list[Event] = []
    ran: list[str] = []

    def receive(_session: Session, event: Event) -> None:
        received.append(event)

    register_subscriber(WORKER_EVENT, receive, consumer="test-failing")
    register_job_handler(direct, recorder(ran))
    register_job_handler(scheduled, recorder(ran))
    _ = publish_due(tenant_id)
    _ = enqueue(queue, tenant_id, direct)
    due_schedule(queue, tenant_id, scheduled)

    def unreachable() -> int:
        msg = "database unreachable"
        raise RuntimeError(msg)

    owner, method = {
        "dispatch": (bus, "dispatch_pending"),
        "jobs": (runner, "run_once"),
        "schedules": (scheduler, "tick"),
    }[failing]
    monkeypatch.setattr(owner, method, unreachable)
    caplog.set_level(logging.ERROR, logger="xolotl.worker")

    worker = Worker(bus=bus, runner=runner, scheduler=scheduler, tick_seconds=0)
    assert worker.run(max_ticks=2) == 2

    worked = {
        "dispatch": len(received) == 1,
        "jobs": direct in ran,
        "schedules": bool(job_statuses(tenant_id, scheduled)),
    }
    assert worked == {step: step != failing for step in worked}
    failures = [r for r in caplog.records if r.name == "xolotl.worker"]
    assert [vars(r)["worker_step"] for r in failures] == [failing, failing]
    assert all(r.levelno == logging.ERROR and r.exc_info for r in failures)


def test_a_negative_tick_is_refused(engine: Engine):
    bus, runner, scheduler, _ = loop_parts()
    with pytest.raises(ValueError, match="tick_seconds"):
        _ = Worker(bus=bus, runner=runner, scheduler=scheduler, tick_seconds=-1)


# ---- between ticks --------------------------------------------------------------


def _pause_of(worker: Worker) -> Callable[[], None]:
    pause = cast("Callable[[Worker], None]", vars(Worker)["_pause"])
    return lambda: pause(worker)


def test_the_loop_holds_no_database_connection_while_it_pauses(
    engine: Engine, quick_ticks: None, monkeypatch: pytest.MonkeyPatch
):
    tenant_id = make_tenant("alpha")
    queue = PostgresJobQueue(get_sessionmaker())
    kind = unique_kind("busy")
    ran: list[str] = []
    register_job_handler(kind, recorder(ran))
    register_subscriber(WORKER_EVENT, lambda _s, _e: None, consumer="test-busy")
    _ = publish_due(tenant_id)
    _ = enqueue(queue, tenant_id, kind)
    worker = build_worker()
    pause = _pause_of(worker)
    held: list[int] = []

    def counting_pause() -> None:
        held.append(cast("QueuePool", get_engine().pool).checkedout())
        pause()

    monkeypatch.setattr(worker, "_pause", counting_pause)
    assert worker.run(max_ticks=3) == 3
    assert ran == [kind]  # the ticks did use the database
    assert held == [0, 0]


def test_stop_ends_the_pause_without_waiting_out_the_tick(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    # With an hour between looks at the flag, only stop() waking the pause itself can
    # end it within the test's patience.
    monkeypatch.setattr(worker_module, "POLL_SECONDS", 3600.0)
    bus, runner, scheduler, _ = loop_parts()
    worker = Worker(bus=bus, runner=runner, scheduler=scheduler, tick_seconds=3600)
    pause, paused = _pause_of(worker), threading.Event()

    def pause_and_tell() -> None:
        paused.set()
        pause()

    monkeypatch.setattr(worker, "_pause", pause_and_tell)
    ticks: list[int] = []
    loop = threading.Thread(target=lambda: ticks.append(worker.run()), daemon=True)
    loop.start()
    assert paused.wait(10)
    worker.stop()
    loop.join(10)
    assert not loop.is_alive()
    assert ticks == [1]


def test_a_worker_stopped_before_it_starts_runs_no_tick(engine: Engine):
    bus, runner, scheduler, _ = loop_parts()
    worker = Worker(bus=bus, runner=runner, scheduler=scheduler, tick_seconds=0)
    worker.stop()
    assert worker.run() == 0


# ---- signals ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "number", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"]
)
def test_a_stop_signal_ends_the_loop_after_the_tick_it_arrives_in(
    engine: Engine, quick_ticks: None, fallback: Fallback, number: signal.Signals
):
    tenant_id = make_tenant("alpha")
    queue = PostgresJobQueue(get_sessionmaker())
    direct, scheduled = unique_kind("direct"), unique_kind("scheduled")
    ran: list[str] = []
    register_job_handler(direct, recorder(ran))
    register_job_handler(scheduled, recorder(ran))

    def sends_the_signal(_session: Session, _event: Event) -> None:
        signal.raise_signal(number)

    register_subscriber(WORKER_EVENT, sends_the_signal, consumer="test-signal")
    _ = publish_due(tenant_id)
    _ = enqueue(queue, tenant_id, direct)
    due_schedule(queue, tenant_id, scheduled)
    worker = build_worker()

    with worker.handle_signals():
        ticks = worker.run(max_ticks=5)

    assert ticks == 1
    # The signal came while events were handed out. The rest of that tick still ran:
    # the due job, then the schedule, whose job waits for a tick that never came.
    assert ran == [direct]
    assert job_statuses(tenant_id, scheduled) == [JobStatus.PENDING]
    assert fallback.caught == []  # the worker's handler took it
    assert signal.getsignal(number) == fallback.handler  # and is gone again


def test_a_stop_signal_during_the_pause_ends_it(
    engine: Engine, fallback: Fallback, monkeypatch: pytest.MonkeyPatch
):
    bus, runner, scheduler, _ = loop_parts()
    worker = Worker(bus=bus, runner=runner, scheduler=scheduler, tick_seconds=3600)
    pause, paused = _pause_of(worker), threading.Event()

    def pause_and_tell() -> None:
        paused.set()
        pause()

    monkeypatch.setattr(worker, "_pause", pause_and_tell)
    main = threading.main_thread().ident
    assert main is not None
    finished, rescued = threading.Event(), threading.Event()

    def send() -> None:
        if paused.wait(10):
            signal.pthread_kill(main, signal.SIGTERM)
        if not finished.wait(10):  # the signal did not end the pause
            rescued.set()
            worker.stop()

    sender = threading.Thread(target=send, daemon=True)
    sender.start()
    with worker.handle_signals():
        ticks = worker.run()
    finished.set()
    sender.join(10)
    assert (ticks, rescued.is_set()) == (1, False)
    assert fallback.caught == []


# ---- the embedded worker -----------------------------------------------------------


def test_the_api_runs_the_worker_on_a_daemon_thread_while_it_serves(
    engine: Engine, embedded_on: None
):
    app = build_app()
    assert embedded_threads() == []
    with TestClient(app) as client:
        (thread,) = embedded_threads()
        assert thread.is_alive()
        assert thread.daemon
        assert client.get("/healthz").status_code == 200
    assert not thread.is_alive()  # joined on shutdown
    assert embedded_threads() == []


def test_with_the_embedded_worker_off_no_thread_starts(
    engine: Engine, embedded_off: None
):
    with TestClient(build_app()) as client:
        assert client.get("/healthz").status_code == 200
        assert embedded_threads() == []


def test_the_suite_builds_its_apps_without_the_worker_thread(engine: Engine):
    # tests/conftest.py turns XOLOTL_EMBEDDED_WORKER off for the whole suite, so the
    # shared `app` and `client` fixtures run no thread beside the tests that drive the
    # queue by hand (the webhook route tests among them). Tests of the embedded worker
    # turn it on for themselves, with the `embedded_on` fixture.
    assert get_settings().embedded_worker is False
    app = build_app()
    with TestClient(app):
        assert embedded_threads() == []
        assert cast("AppState", app.state.xolotl).embedded_worker_alive is None


def test_the_embedded_worker_works_through_the_apps_session_factory(
    engine: Engine, embedded_on: None
):
    opened_on: list[str] = []

    def factory() -> Session:
        opened_on.append(threading.current_thread().name)
        return get_sessionmaker()()

    received: list[Event] = []

    def receive(_session: Session, event: Event) -> None:
        received.append(event)

    register_subscriber("test.ping.v1", receive, consumer="test-embedded")
    tenant = new_tenant()
    with TestClient(build_app(session_factory=factory)) as client:
        headers = first_login(
            client, tenant.slug, tenant.admin_email, tenant.admin_password, PASSWORD
        )
        pong = client.post(
            "/v1/ping", json={}, headers={**headers, "Idempotency-Key": "e1"}
        )
        assert pong.status_code == 200, pong.text
        make_due(tenant.tenant_id)
        assert eventually(lambda: len(received) == 1)
    assert [event.key for event in received] == [str(tenant.admin_user_id)]
    # One pool: the worker thread's sessions came from the factory the app serves with.
    assert EMBEDDED_THREAD_NAME in opened_on


def test_a_worker_that_does_not_stop_in_time_is_left_to_its_lease(
    engine: Engine, caplog: pytest.LogCaptureFixture
):
    tenant_id = make_tenant("alpha")
    entered, release = threading.Event(), threading.Event()

    def slow(_session: Session, _event: Event) -> None:
        entered.set()
        _ = release.wait(30)

    register_subscriber(WORKER_EVENT, slow, consumer="test-slow")
    _ = publish_due(tenant_id)
    bus, runner, scheduler, _ = loop_parts()
    embedded = EmbeddedWorker(
        Worker(bus=bus, runner=runner, scheduler=scheduler, tick_seconds=0),
        stop_timeout=0.05,
    )
    caplog.set_level(logging.INFO, logger="xolotl.worker")
    embedded.start()
    try:
        assert entered.wait(10)
        assert embedded.stop() is False  # the wait is bounded
        assert embedded.thread.is_alive()
        (warning,) = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert "lease" in warning.getMessage()
    finally:
        release.set()
        embedded.thread.join(10)
    assert not embedded.thread.is_alive()


def test_an_embedded_worker_that_dies_says_so(
    engine: Engine, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    class ProcessDiedError(BaseException):
        """Not an ``Exception``: the loop does not contain it."""

    def dies() -> int:
        raise ProcessDiedError

    bus, runner, scheduler, _ = loop_parts()
    monkeypatch.setattr(bus, "dispatch_pending", dies)
    embedded = EmbeddedWorker(
        Worker(bus=bus, runner=runner, scheduler=scheduler, tick_seconds=0)
    )
    caplog.set_level(logging.ERROR, logger="xolotl.worker")
    embedded.start()
    embedded.thread.join(10)
    assert not embedded.thread.is_alive()
    (record,) = [r for r in caplog.records if r.name == "xolotl.worker"]
    assert record.getMessage().startswith("embedded worker stopped by an error")
    assert record.exc_info is not None


# ---- wiring -------------------------------------------------------------------------


def test_the_platform_is_wired_with_its_job_handlers(engine: Engine):
    services = wire_platform(get_sessionmaker(), get_settings())
    assert job_handler(PURGE_KIND) is purge_expired_keys
    assert job_handler(ADVANCE_KIND) == services.workflows.handle_job
    assert job_handler(TIMER_KIND) == services.workflows.handle_job
    assert job_handler(SWEEP_KIND) == services.workflows.handle_sweep
    assert isinstance(
        getattr(job_handler(UNITS_FETCH_KIND), "__self__", None), UnitsFetchJob
    )
    lists_poll = getattr(job_handler(LISTS_POLL_KIND), "__self__", None)
    assert isinstance(lists_poll, ListsPollJob)
    # XOLOTL_ENV=test leaves XOLOTL_LISTS_FETCH_ENABLED unset: no network fetching.
    assert lists_poll.fetching is False
    assert job_handler(SCREEN_PARTY_KIND) is screen_party_job
    assert SCREENING_CONSUMER in consumers()
    assert isinstance(getattr(job_handler(PROCESS_KIND), "__self__", None), ProcessJob)
    assert isinstance(
        getattr(job_handler(INGEST_FILE_KIND), "__self__", None), IngestFileJob
    )
    for kind, handler in (
        (MONITORING_SWEEP_KIND, SweepJob),
        (BACKFILL_KIND, BackfillJob),
        (AGG_BACKFILL_KIND, AggBackfillJob),
        (PARTITIONS_KIND, PartitionsJob),
    ):
        assert isinstance(getattr(job_handler(kind), "__self__", None), handler)
    assert job_handler(SIMULATE_KIND) is handle_simulate


def test_the_api_keeps_one_set_of_platform_services(engine: Engine):
    state = cast("AppState", build_app().state.xolotl)
    assert state.services is not None
    assert state.services.session_factory is state.session_factory
    assert state.object_store is state.services.object_store


def test_a_new_tenants_hourly_jobs_run_through_the_worker(
    engine: Engine, quick_ticks: None
):
    created = new_tenant()
    now = clock.now()
    with tenant_session(system_ctx(created.tenant_id)) as s:
        for key, expires_at in (
            ("expired", now - timedelta(minutes=5)),
            ("live", now + timedelta(hours=1)),
        ):
            s.add(
                IdempotencyKey(
                    tenant_id=created.tenant_id,
                    actor_id="a",
                    key=key,
                    request_hash=b"",
                    status_code=200,
                    response_body={},
                    expires_at=expires_at,
                )
            )
        _ = s.execute(
            update(JobSchedule).values(next_run_at=now - timedelta(minutes=1))
        )

    # The first tick enqueues both jobs, the second runs them.
    assert build_worker().run(max_ticks=2) == 2

    with tenant_session(system_ctx(created.tenant_id)) as s:
        keys = set(s.scalars(select(IdempotencyKey.key)))
        rows = s.scalars(select(JobRow)).all()
    # The scheduled jobs carry {}; the monthly one queued the first page of its
    # batch, which a later tick runs.
    jobs = {row.kind: row.status for row in rows if row.payload == {}}
    assert keys == {"live"}
    assert jobs == {
        PURGE_KIND: JobStatus.DONE,
        LISTS_POLL_KIND: JobStatus.DONE,
        "party.retention_sweep": JobStatus.DONE,
        # Every slot was made due, the monthly one too.
        "screening.periodic_rescreen": JobStatus.DONE,
        UNITS_FETCH_KIND: JobStatus.DONE,
        SWEEP_KIND: JobStatus.DONE,
        MONITORING_SWEEP_KIND: JobStatus.DONE,
        PARTITIONS_KIND: JobStatus.DONE,
        # A tenant without a policy retires nothing, and the job still completes.
        "monitoring.retention": JobStatus.DONE,
        # A tenant without processed rows profiles nothing.
        "monitoring.profiles": JobStatus.DONE,
    }


# ---- xolotl worker ----------------------------------------------------------------


def test_the_worker_command_runs_the_loop_until_sigterm(
    engine: Engine,
    quick_ticks: None,
    fallback: Fallback,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant_id = make_tenant("alpha")

    def sends_sigterm(_session: Session, _event: Event) -> None:
        signal.raise_signal(signal.SIGTERM)

    register_subscriber(WORKER_EVENT, sends_sigterm, consumer="test-cli")
    event = publish_due(tenant_id)
    run = cast("Callable[..., int]", vars(Worker)["run"])
    ticks: list[int] = []

    def bounded(self: Worker, max_ticks: int | None = None) -> int:
        # A bound, so that a worker deaf to SIGTERM fails the test instead of hanging.
        assert max_ticks is None
        ticks.append(run(self, max_ticks=50))
        return ticks[-1]

    monkeypatch.setattr(Worker, "run", bounded)
    result = CliRunner().invoke(cli, ["worker"])
    assert result.exit_code == 0, result.output
    assert ticks == [1]
    assert outbox_status(event.id, tenant_id) is OutboxStatus.PUBLISHED
    assert fallback.caught == []
    assert signal.getsignal(signal.SIGTERM) == fallback.handler


def test_the_worker_command_refuses_a_role_that_can_bypass_rls(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    def bypassing(_target: Engine) -> None:
        msg = "database role can bypass row-level security (superuser or BYPASSRLS)"
        raise RoleBypassError(msg)

    ran: list[int] = []

    def run(_self: Worker, max_ticks: int | None = None) -> int:
        ran.append(1)
        return 0

    monkeypatch.setattr("xolotl.cli.assert_app_role_safe", bypassing)
    monkeypatch.setattr(Worker, "run", run)
    result = CliRunner().invoke(cli, ["worker"])
    assert result.exit_code == 1
    assert "can bypass row-level security" in result.output
    assert ran == []


# ---- fix round 1: start-up schedules, readiness, max_ticks, a second signal ---------

PLATFORM_SCHEDULES = [
    ("idempotency.purge", "0 * * * *", "UTC", "idempotency.purge", True),
    ("lists.poll", "* * * * *", "UTC", "lists.poll", True),
    ("monitoring.partitions", "0 1 * * *", "UTC", "monitoring.partitions", True),
    (
        "monitoring.profiles",
        "0 3 2 * *",
        "America/Mexico_City",
        "monitoring.profiles",
        True,
    ),
    ("monitoring.retention", "0 5 1 * *", "UTC", "monitoring.retention", True),
    ("monitoring.sweep", "* * * * *", "UTC", "monitoring.sweep", True),
    ("party.retention_sweep", "0 3 * * *", "UTC", "party.retention_sweep", True),
    (
        "screening.periodic_rescreen",
        "0 2 1 * *",
        "America/Mexico_City",
        "screening.periodic_rescreen",
        True,
    ),
    ("units.fetch", "0 18 * * 1-5", "America/Mexico_City", "units.fetch", True),
    ("workflow.sweep", "0 * * * *", "UTC", "workflow.sweep", True),
]


def schedules_of(tenant_id: UUID) -> list[tuple[str, str, str, str, bool]]:
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(select(JobSchedule).order_by(JobSchedule.name)).all()
        return [(r.name, r.cron, r.tz, r.kind, r.enabled) for r in rows]


def test_a_starting_loop_gives_older_tenants_the_platforms_schedules(
    engine: Engine, quick_ticks: None
):
    legacy = make_tenant("legacy")  # created as plans 1a and 1b did: no schedules
    assert schedules_of(legacy) == []
    assert build_worker().run(max_ticks=0) == 0  # the start-up step, and no tick
    assert schedules_of(legacy) == PLATFORM_SCHEDULES


def test_declaring_the_schedules_again_keeps_every_slot(
    engine: Engine, quick_ticks: None
):
    created = new_tenant()
    overdue = clock.now() - timedelta(minutes=30)
    with tenant_session(system_ctx(created.tenant_id)) as s:
        _ = s.execute(
            update(JobSchedule)
            .where(JobSchedule.name == PURGE_KIND)
            .values(next_run_at=overdue)
        )
    with tenant_session(system_ctx(created.tenant_id)) as s:
        before = {r.name: r.next_run_at for r in s.scalars(select(JobSchedule))}
    assert build_worker().run(max_ticks=0) == 0
    with tenant_session(system_ctx(created.tenant_id)) as s:
        after = {r.name: r.next_run_at for r in s.scalars(select(JobSchedule))}
    assert after == before  # one row each, and no slot postponed
    assert after[PURGE_KIND] == overdue


def test_the_embedded_worker_declares_them_when_the_api_starts(
    engine: Engine, embedded_on: None
):
    legacy = make_tenant("legacy")
    with TestClient(build_app()):
        assert eventually(lambda: schedules_of(legacy) == PLATFORM_SCHEDULES)


def test_a_start_up_step_that_fails_is_logged_and_the_loop_still_runs(
    engine: Engine, caplog: pytest.LogCaptureFixture
):
    bus, runner, scheduler, _ = loop_parts()

    def cannot_declare() -> None:
        msg = "database unreachable"
        raise RuntimeError(msg)

    worker = Worker(
        bus=bus,
        runner=runner,
        scheduler=scheduler,
        tick_seconds=0,
        on_start=cannot_declare,
    )
    caplog.set_level(logging.ERROR, logger="xolotl.worker")
    assert worker.run(max_ticks=1) == 1
    (record,) = [r for r in caplog.records if r.name == "xolotl.worker"]
    assert record.getMessage() == "worker start-up step failed"
    assert record.exc_info is not None


def test_the_api_is_ready_while_its_embedded_worker_runs(
    engine: Engine, embedded_on: None
):
    with TestClient(build_app()) as client:
        (thread,) = embedded_threads()
        assert thread.is_alive()
        ready = client.get("/readyz")
        assert (ready.status_code, ready.json()) == (
            200,
            {"status": "ok", "database": "ok"},
        )


def test_the_api_is_not_ready_once_its_embedded_worker_has_died(
    engine: Engine, embedded_on: None, monkeypatch: pytest.MonkeyPatch
):
    class ProcessDiedError(BaseException):
        """Not an ``Exception``: it ends the loop."""

    def dies(_self: OutboxEventBus, *, batch: int = 200) -> int:
        raise ProcessDiedError

    monkeypatch.setattr(OutboxEventBus, "dispatch_pending", dies)
    with TestClient(build_app()) as client:
        assert eventually(lambda: embedded_threads() == [])  # the loop is gone
        not_ready = client.get("/readyz")
        assert not_ready.status_code == 503
        assert (not_ready.json()["code"], not_ready.json()["detail"]) == (
            "NOT_READY",
            "embedded worker stopped",
        )


def test_an_api_without_an_embedded_worker_is_ready(engine: Engine, embedded_off: None):
    with TestClient(build_app()) as client:
        ready = client.get("/readyz")
        assert (ready.status_code, ready.json()) == (
            200,
            {"status": "ok", "database": "ok"},
        )


def test_max_ticks_zero_runs_no_tick(engine: Engine, quick_ticks: None):
    tenant_id = make_tenant("alpha")
    queue = PostgresJobQueue(get_sessionmaker())
    kind = unique_kind("never")
    ran: list[str] = []
    register_job_handler(kind, recorder(ran))
    _ = enqueue(queue, tenant_id, kind)
    assert build_worker().run(max_ticks=0) == 0
    assert ran == []
    assert job_statuses(tenant_id, kind) == [JobStatus.PENDING]


def test_a_negative_max_ticks_is_refused(engine: Engine):
    bus, runner, scheduler, _ = loop_parts()
    worker = Worker(bus=bus, runner=runner, scheduler=scheduler, tick_seconds=0)
    with pytest.raises(ValueError, match="max_ticks"):
        _ = worker.run(max_ticks=-1)


@pytest.mark.parametrize(
    "number", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"]
)
def test_a_second_stop_signal_forces_the_exit_mid_tick(
    engine: Engine, quick_ticks: None, fallback: Fallback, number: signal.Signals
):
    tenant_id = make_tenant("alpha")
    queue = PostgresJobQueue(get_sessionmaker())
    direct = unique_kind("direct")
    ran: list[str] = []
    register_job_handler(direct, recorder(ran))

    def signals_twice(_session: Session, _event: Event) -> None:
        signal.raise_signal(number)  # stop after this tick...
        signal.raise_signal(number)  # ...no: stop now

    register_subscriber(WORKER_EVENT, signals_twice, consumer="test-twice")
    event = publish_due(tenant_id)
    _ = enqueue(queue, tenant_id, direct)
    worker = build_worker()

    with pytest.raises(SystemExit) as forced, worker.handle_signals():
        _ = worker.run(max_ticks=5)

    assert forced.value.code == 128 + number
    # The tick was cut short: the handler's transaction rolled back, so the event is
    # still pending (its lease will run out), and the due job never ran.
    assert outbox_status(event.id, tenant_id) is OutboxStatus.PENDING
    assert ran == []
    assert fallback.caught == []
    assert signal.getsignal(number) == fallback.handler
