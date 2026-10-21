"""The ``monitoring.agg_backfill`` job: a declaration's history rebuilt (A9)."""

import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.orm import Session

from tests.helpers import staff_session, system_ctx
from tests.modules.monitoring.support import (
    add_customer,
    ingest_items,
    load_missing_values,
    make_item,
)
from xolotl.modules.monitoring.aggregates import backfill as backfill_module
from xolotl.modules.monitoring.aggregates.backfill import (
    AggBackfillJob,
    history_start,
    share_aggregate_epoch,
)
from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    Measure,
    WindowKind,
)
from xolotl.modules.monitoring.aggregates.models import (
    AggBucket,
    AggDeclaration,
    AggDistinct,
)
from xolotl.modules.monitoring.aggregates.postgres import (
    PostgresAggregateStore,
    ensure_declarations,
)
from xolotl.modules.monitoring.aggregates.store import (
    AccumulatorSplitError,
    AggregateKey,
)
from xolotl.modules.monitoring.enums import BucketKind
from xolotl.modules.monitoring.kinds import AGG_BACKFILL_KIND
from xolotl.modules.monitoring.process import job as job_module
from xolotl.modules.monitoring.process.job import process_party
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import KeyKind
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import Settings, get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import Job, PostgresJobQueue

INGESTED = datetime(2026, 1, 22, 20, tzinfo=UTC)
PROCESSED = datetime(2026, 1, 23, 9, tzinfo=UTC)
# Every row below was processed before this cutoff and occurred after its
# history start (2025-07-24).
CUTOFF = datetime(2026, 1, 25, 12, tzinfo=UTC)
LATER = datetime(2026, 1, 26, 9, tzinfo=UTC)
STREAM = (
    datetime(2026, 1, 20, 12, 10, tzinfo=UTC),
    datetime(2026, 1, 20, 13, 10, tzinfo=UTC),
    datetime(2026, 1, 22, 18, 10, tzinfo=UTC),
)
COUNT_P7D = AggregateDeclaration(
    name="count_p7d",
    function=AggregateFunction.COUNT,
    measure=Measure.ONE,
    unit_key=None,
    key_kind=KeyKind.PARTY,
    window_kind=WindowKind.DURATION,
    window="P7D",
)
COUNTERPARTIES_P7D = AggregateDeclaration(
    name="counterparties_p7d",
    function=AggregateFunction.DISTINCT_COUNT,
    measure=Measure.ONE,
    unit_key=None,
    key_kind=KeyKind.PARTY,
    window_kind=WindowKind.DURATION,
    window="P7D",
    distinct_of="COUNTERPARTY",
)


@pytest.fixture
def customer(tenant: CreatedTenant) -> CustomerRelationship:
    return add_customer(tenant, "cust-1")


@pytest.fixture
def history(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    itf_tenant: UUID,
    admin_engine: Engine,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> list[UUID]:
    """Process three rows before any declaration exists.

    Returns:
        Their ids, in ``occurred_at`` order.

    """
    load_missing_values(admin_engine)
    items = [
        make_item(
            external_id=f"tx-{n}",
            occurred_at=at.isoformat(),
            counterparty={
                "kind": "PERSON",
                "name": f"Persona {n % 2}",
                "country": "MX",
            },
        )
        for n, at in enumerate(STREAM, start=1)
    ]
    ids = _ingest(tenant, queue, monkeypatch, items)
    _drain(tenant, customer.party_id, queue, monkeypatch, now=PROCESSED)
    return ids


def _ingest(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
    items: Sequence[dict[str, object]],
) -> list[UUID]:
    with monkeypatch.context() as patched:
        patched.setattr(clock, "now", lambda: INGESTED)
        with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
            report = ingest_items(s, items, queue=queue)
    return list(report.transaction_ids)


def _drain(
    tenant: CreatedTenant,
    party_id: UUID,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
    *,
    now: datetime,
) -> None:
    with monkeypatch.context() as patched:
        patched.setattr(clock, "now", lambda: now)
        report = process_party(
            get_sessionmaker(),
            tenant.tenant_id,
            party_id,
            queue=queue,
            job=None,
            settings=get_settings(),
            actor="cli:test",
        )
    assert report.parked == 0


def _declare(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    declaration: AggregateDeclaration = COUNT_P7D,
) -> str:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        (agg_id,) = ensure_declarations(s, [declaration], queue=queue)
    return agg_id


def _job(batch: int = 5000, *, at: datetime = CUTOFF) -> AggBackfillJob:
    settings: Settings = get_settings().model_copy(
        update={"monitoring_agg_backfill_batch": batch}
    )
    return AggBackfillJob(
        session_factory=get_sessionmaker(),
        settings=settings,
        queue=PostgresJobQueue(get_sessionmaker(), clock_fn=lambda: at),
        clock_fn=lambda: at,
    )


def _backfill(tenant: CreatedTenant, job: AggBackfillJob, agg_id: str) -> int | None:
    return job.backfill(tenant.tenant_id, agg_id, actor="job:test")


def _counted(tenant: CreatedTenant, agg_id: str) -> int:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return int(
            s.scalar(
                select(func.coalesce(func.sum(AggBucket.count), 0)).where(
                    AggBucket.agg_id == agg_id
                )
            )
            or 0
        )


def _through(tenant: CreatedTenant, agg_id: str) -> datetime | None:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return s.scalar(
            select(AggDeclaration.backfilled_through).where(
                AggDeclaration.agg_id == agg_id
            )
        )


def test_the_walk_starts_six_months_back_on_a_utc_midnight():
    assert history_start(datetime(2026, 7, 22, 15, tzinfo=UTC)) == datetime(
        2026, 1, 21, tzinfo=UTC
    )
    # 31 August minus six months is the last day of February.
    assert history_start(datetime(2026, 8, 31, 1, tzinfo=UTC)) == datetime(
        2026, 2, 27, tzinfo=UTC
    )


def test_a_new_declaration_is_backfilled_from_six_months_of_rows_and_completes(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    history: list[UUID],
):
    agg_id = _declare(tenant, queue)
    key = AggregateKey(KeyKind.PARTY, str(customer.party_id))
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        before = PostgresAggregateStore(s).read(key, COUNT_P7D, at=STREAM[-1])
    assert (before.count, before.complete) == (0, False)
    # Half a year after the stream, the rows of the 20th are past the history.
    late = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _job(at=late)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        job.run(s, _session_job(tenant, agg_id))
    assert _counted(tenant, agg_id) == 1
    assert _through(tenant, agg_id) == late
    # Within six months, every row counts; a complete declaration is a no-op.
    agg_id_all = _declare(tenant, queue, COUNTERPARTIES_P7D)
    assert _backfill(tenant, _job(), agg_id_all) == 3
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        read = PostgresAggregateStore(s).read(key, COUNTERPARTIES_P7D, at=STREAM[-1])
    assert (read.distinct, read.count, read.complete) == (2, 3, True)
    assert _backfill(tenant, _job(), agg_id_all) == 0
    assert _backfill(tenant, _job(), "0" * 16) == 0


def test_the_walk_first_deletes_the_declarations_buckets_and_members(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    history: list[UUID],
):
    agg_id = _declare(tenant, queue, COUNTERPARTIES_P7D)
    start = datetime(2026, 1, 20, 12, tzinfo=UTC)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        s.add(
            AggBucket(
                tenant_id=tenant.tenant_id,
                key_kind=KeyKind.PARTY,
                key=str(customer.party_id),
                agg_id=agg_id,
                bucket_start=start,
                bucket_kind=BucketKind.HOUR,
                count=40,
            )
        )
        s.add(
            AggDistinct(
                tenant_id=tenant.tenant_id,
                key_kind=KeyKind.PARTY,
                key=str(customer.party_id),
                agg_id=agg_id,
                bucket_start=start,
                member_hash=b"\x00" * 32,
            )
        )
    _ = _backfill(tenant, _job(), agg_id)
    assert _counted(tenant, agg_id) == 3
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        members = s.scalars(
            select(AggDistinct.member_hash).where(AggDistinct.agg_id == agg_id)
        ).all()
    assert len(members) == 3
    assert b"\x00" * 32 not in members


def test_a_backfill_page_is_one_transaction_and_the_job_resumes(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    history: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
):
    agg_id = _declare(tenant, queue)
    original = vars(backfill_module)["enriched_from_row"]
    calls: list[UUID] = []

    def crash_on_the_second_page(row: Transaction):
        calls.append(row.id)
        if len(calls) == 2:
            msg = "the worker died"
            raise RuntimeError(msg)
        return original(row)

    with monkeypatch.context() as patched:
        patched.setattr(backfill_module, "enriched_from_row", crash_on_the_second_page)
        with pytest.raises(RuntimeError, match="the worker died"):
            _ = _backfill(tenant, _job(batch=1), agg_id)
    # The first page committed; the declaration is still incomplete.
    assert _counted(tenant, agg_id) == 1
    assert _through(tenant, agg_id) is None
    # The retry deletes and walks again: nothing counts twice.
    assert _backfill(tenant, _job(batch=1), agg_id) == 3
    assert _counted(tenant, agg_id) == 3
    assert _through(tenant, agg_id) == CUTOFF


def _new_row(
    tenant: CreatedTenant, queue: PostgresJobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = make_item(external_id="tx-new", occurred_at="2026-01-20T12:40:00+00:00")
    _ = _ingest(tenant, queue, monkeypatch, [item])


def test_rows_processed_during_the_walk_are_not_added_twice(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    history: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
):
    agg_id = _declare(tenant, queue)
    _new_row(tenant, queue, monkeypatch)
    original = vars(AggBackfillJob)["_page"]
    pages: list[int] = []

    def drain_meanwhile(self: AggBackfillJob, s: Session, step, after):
        pages.append(1)
        if len(pages) == 2:
            # Processed after the cutoff, and earlier than the walk's position:
            # its own drain adds it, the walk does not read it.
            _drain(tenant, customer.party_id, queue, monkeypatch, now=LATER)
        return original(self, s, step, after)

    monkeypatch.setattr(AggBackfillJob, "_page", drain_meanwhile)
    assert _backfill(tenant, _job(batch=1), agg_id) == 3
    assert _counted(tenant, agg_id) == 4


_BLOCKED_BY = text(
    "SELECT count(*) FROM pg_stat_activity WHERE :pid = ANY(pg_blocking_pids(pid))"
)


def _until(condition: Callable[[], bool], what: str, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            msg = f"timed out waiting until {what}"
            raise AssertionError(msg)
        time.sleep(0.01)


def _blocked_by(admin_engine: Engine, pid: int) -> bool:
    with admin_engine.connect() as conn:
        return conn.execute(_BLOCKED_BY, {"pid": pid}).scalar_one() > 0


def test_a_drain_concurrent_with_an_agg_backfill_waits_and_neither_deadlocks(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    history: list[UUID],
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
):
    # Plan 4a, note from A8: a drain row and a backfill page upsert the same
    # bucket; one waits for the other and both finish.
    agg_id = _declare(tenant, queue)
    _new_row(tenant, queue, monkeypatch)
    added = threading.Event()
    release = threading.Event()
    walk_pid: list[int] = []
    original = vars(backfill_module)["_add_page"]

    def hold_the_page(s: Session, rows, declaration):
        done = original(s, rows, declaration)
        walk_pid.append(s.execute(text("SELECT pg_backend_pid()")).scalar_one())
        added.set()
        _ = release.wait(30)
        return done

    monkeypatch.setattr(backfill_module, "_add_page", hold_the_page)
    # The drain stamps processed_at after the cutoff; the walk reads its own clock.
    monkeypatch.setattr(clock, "now", lambda: LATER)
    with ThreadPoolExecutor(max_workers=2) as pool:
        walk = pool.submit(_backfill, tenant, _job(), agg_id)
        assert added.wait(30)
        drain = pool.submit(
            process_party,
            get_sessionmaker(),
            tenant.tenant_id,
            customer.party_id,
            queue=queue,
            job=None,
            settings=get_settings(),
            actor="cli:test",
        )
        _until(
            lambda: _blocked_by(admin_engine, walk_pid[0]),
            "the drain waits on the page's bucket",
        )
        release.set()
        assert walk.result(timeout=30) == 3
        assert drain.result(timeout=30).processed == 1
    assert _counted(tenant, agg_id) == 4
    assert _through(tenant, agg_id) == CUTOFF


def _split(tenant: CreatedTenant, agg_id: str) -> None:
    # What a late row that cannot split an accumulator does to its declaration.
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = s.execute(
            update(AggDeclaration)
            .where(AggDeclaration.agg_id == agg_id)
            .values(backfilled_through=None)
        )


def test_a_split_during_the_walk_starts_the_round_again(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    history: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
):
    agg_id = _declare(tenant, queue)
    original = vars(AggBackfillJob)["_start"]
    starts: list[int] = []

    def split_in_the_first_round(self: AggBackfillJob, ctx, agg: str):
        step = original(self, ctx, agg)
        starts.append(1)
        if len(starts) == 1:
            _split(tenant, agg_id)
        return step

    monkeypatch.setattr(AggBackfillJob, "_start", split_in_the_first_round)
    assert _backfill(tenant, _job(), agg_id) == 3
    # Two rounds, and the start that found the declaration complete.
    assert len(starts) == 3
    assert _counted(tenant, agg_id) == 3
    assert _through(tenant, agg_id) == CUTOFF


def test_a_walk_whose_add_meets_a_split_starts_again(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    history: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
):
    agg_id = _declare(tenant, queue)
    original = PostgresAggregateStore.add
    calls: list[int] = []

    def split_once(self: PostgresAggregateStore, txn, declarations):
        calls.append(1)
        if len(calls) == 2:
            msg = "split"
            raise AccumulatorSplitError(msg)
        original(self, txn, declarations)

    monkeypatch.setattr(PostgresAggregateStore, "add", split_once)
    assert _backfill(tenant, _job(), agg_id) == 3
    assert _counted(tenant, agg_id) == 3
    assert _through(tenant, agg_id) == CUTOFF


def _session_job(tenant: CreatedTenant, agg_id: str, **payload: int) -> Job:
    return Job(
        id=uuid7(),
        tenant_id=tenant.tenant_id,
        kind=AGG_BACKFILL_KIND,
        key=f"agg_backfill:{agg_id}",
        payload={"agg_id": agg_id, **payload},
        attempts=1,
        max_attempts=5,
        lock_token=uuid7(),
    )


def _follow_ups(tenant: CreatedTenant, agg_id: str) -> list[JobRow]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return list(
            s.scalars(
                select(JobRow).where(
                    JobRow.kind == AGG_BACKFILL_KIND,
                    JobRow.key.startswith(f"agg_backfill:{agg_id}:after:"),
                )
            )
        )


def test_splits_that_never_stop_defer_the_run_with_a_backoff(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    history: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    agg_id = _declare(tenant, queue)
    original = vars(AggBackfillJob)["_start"]

    def always_split(self: AggBackfillJob, ctx, agg: str):
        step = original(self, ctx, agg)
        _split(tenant, agg_id)
        return step

    monkeypatch.setattr(AggBackfillJob, "_start", always_split)
    assert _backfill(tenant, _job(), agg_id) is None
    assert _through(tenant, agg_id) is None
    assert any(
        getattr(record, "agg_id", None) == agg_id
        and getattr(record, "restarts", None) == backfill_module.MAX_ROUNDS
        for record in caplog.records
    )
    # As a job, the run ends and enqueues its follow-up, deferrals one higher.
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _job().run(s, _session_job(tenant, agg_id, deferrals=1))
    (follow_up,) = _follow_ups(tenant, agg_id)
    assert follow_up.payload == {"agg_id": agg_id, "deferrals": 2}
    assert follow_up.run_after == CUTOFF + timedelta(minutes=4)


def _run(tenant: CreatedTenant, job: AggBackfillJob, session_job: Job) -> None:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        job.run(s, session_job)


def test_a_start_the_tenants_drains_hold_past_its_timeout_is_deferred(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    history: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
):
    agg_id = _declare(tenant, queue)
    monkeypatch.setattr(backfill_module, "START_LOCK_TIMEOUT", "50ms")
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        # A drain row transaction of the tenant, still running.
        share_aggregate_epoch(s, tenant.tenant_id)
        with ThreadPoolExecutor(max_workers=1) as pool:
            deferred = pool.submit(_backfill, tenant, _job(), agg_id)
            assert deferred.result(timeout=30) is None
            # As a job, the run ends and enqueues its follow-up.
            pool.submit(_run, tenant, _job(), _session_job(tenant, agg_id)).result(
                timeout=30
            )
    assert _through(tenant, agg_id) is None
    (follow_up,) = _follow_ups(tenant, agg_id)
    assert follow_up.payload == {"agg_id": agg_id, "deferrals": 1}
    assert follow_up.run_after == CUTOFF + timedelta(minutes=2)
    # Once the drains let go, the start goes through.
    assert _backfill(tenant, _job(), agg_id) == 3
    assert _through(tenant, agg_id) == CUTOFF


def test_a_drain_page_read_before_the_declaration_still_adds_its_rows_once(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    history: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
):
    # The page loads the declarations before ensure_declarations commits; the
    # backfill starts (and runs) before the page's row is processed. The row is
    # processed after the cutoff, so the walk skips it: the row adds itself.
    _new_row(tenant, queue, monkeypatch)
    original = vars(job_module)["_page"]
    declared: list[str] = []

    def declare_after_the_page_loads(*args, **kwargs):
        page = original(*args, **kwargs)
        if not declared:
            declared.append(_declare(tenant, queue))
            assert _backfill(tenant, _job(), declared[0]) == 3
        return page

    monkeypatch.setattr(job_module, "_page", declare_after_the_page_loads)
    _drain(tenant, customer.party_id, queue, monkeypatch, now=LATER)
    assert _counted(tenant, declared[0]) == 4
    assert _through(tenant, declared[0]) == CUTOFF


def test_a_second_run_waits_for_the_first_and_finds_the_declaration_complete(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    history: list[UUID],
    admin_engine: Engine,
):
    agg_id = _declare(tenant, queue)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with tenant_session(system_ctx(tenant.tenant_id)) as s:
            pid = s.execute(text("SELECT pg_backend_pid()")).scalar_one()
            _ = s.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"monitoring.agg_backfill:{tenant.tenant_id}:{agg_id}"},
            )
            second = pool.submit(_backfill, tenant, _job(), agg_id)
            _until(lambda: _blocked_by(admin_engine, pid), "the second run waits")
            # The first run finishes while holding the lock.
            _ = s.execute(
                update(AggDeclaration)
                .where(AggDeclaration.agg_id == agg_id)
                .values(backfilled_through=CUTOFF - timedelta(hours=1))
            )
        assert second.result(timeout=30) == 0
    assert _counted(tenant, agg_id) == 0
    assert _through(tenant, agg_id) == CUTOFF - timedelta(hours=1)
