"""The monitoring sweep: the enqueue race, un-parking, retries and the notice (A9)."""

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import sessionmaker

from tests.helpers import platform_ctx, staff_session, system_ctx
from tests.modules.monitoring.support import (
    add_customer,
    declare,
    ingest_items,
    load_missing_values,
    make_item,
    run_processing,
)
from xolotl.app import wire_platform
from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    Measure,
    TxnFilter,
    WindowKind,
)
from xolotl.modules.monitoring.aggregates.models import AggBucket
from xolotl.modules.monitoring.aggregates.postgres import ensure_declarations
from xolotl.modules.monitoring.enums import FileStatus, ParkReason, TransactionStatus
from xolotl.modules.monitoring.kinds import (
    AGG_BACKFILL_KIND,
    PROCESS_KIND,
    SWEEP_KIND,
    process_key,
)
from xolotl.modules.monitoring.process.job import ProcessReport, process_party
from xolotl.modules.monitoring.process.sweep import (
    PARKED_TEMPLATE,
    SweepReport,
    sweep,
)
from xolotl.modules.monitoring.txn.models import (
    Transaction,
    TransactionFile,
    TransactionParked,
)
from xolotl.modules.monitoring.txn.schema import Instrument, KeyKind
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import Confidence, JobStatus
from xolotl.platform.events import InAppNotifier
from xolotl.platform.events.models import Notification
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import PostgresJobQueue
from xolotl.platform.jobs.runner import JobRunner
from xolotl.platform.jobs.scheduler import Scheduler
from xolotl.platform.policy import assign_pack
from xolotl.platform.policy.models import TenantPackAssignment
from xolotl.platform.services import PlatformServices
from xolotl.platform.units import UnitValue, upsert_value

DAY = datetime(2026, 1, 20, 12, 0, tzinfo=UTC)
RECEIVED = datetime(2026, 1, 21, 5, 0, tzinfo=UTC)
# 2026-01-22 has no UDI value in the fixture.
MISSING = datetime(2026, 1, 22, 18, 0, tzinfo=UTC)
CASH_SUM_P7D = AggregateDeclaration(
    name="cash_sum_p7d",
    function=AggregateFunction.SUM,
    measure=Measure.AMOUNT_MINOR,
    unit_key=None,
    key_kind=KeyKind.PARTY,
    window_kind=WindowKind.DURATION,
    window="P7D",
    where=TxnFilter(instrument=frozenset({Instrument.CASH})),
)


@pytest.fixture
def services(engine: Engine) -> PlatformServices:
    return wire_platform(get_sessionmaker(), get_settings())


@pytest.fixture
def customer_b(tenant: CreatedTenant) -> CustomerRelationship:
    return add_customer(tenant, "cust-2")


def _item(number: int, at: datetime, **overrides: object) -> dict[str, object]:
    return make_item(
        external_id=f"tx-{number}",
        occurred_at=at.isoformat(),
        amount=f"{number}00.00",
        **overrides,
    )


def _ingest(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
    items: Sequence[dict[str, object]],
    *,
    now: datetime,
) -> list[UUID]:
    with monkeypatch.context() as patched:
        patched.setattr(clock, "now", lambda: now)
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
) -> ProcessReport:
    with monkeypatch.context() as patched:
        patched.setattr(clock, "now", lambda: now)
        return process_party(
            get_sessionmaker(),
            tenant.tenant_id,
            party_id,
            queue=queue,
            job=None,
            settings=get_settings(),
            actor="cli:test",
        )


def _sweep(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
    *,
    now: datetime,
) -> SweepReport:
    # The notifier stamps created_at from the platform clock: keep both together.
    with monkeypatch.context() as patched:
        patched.setattr(clock, "now", lambda: now)
        with tenant_session(system_ctx(tenant.tenant_id)) as s:
            return sweep(s, queue=queue, notifier=InAppNotifier(), now=now)


def _settle_processing(tenant: CreatedTenant) -> None:
    """Mark every pending processing job done: the race's lost enqueue."""
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = s.execute(
            update(JobRow)
            .where(
                JobRow.kind == PROCESS_KIND,
                JobRow.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
            )
            .values(status=JobStatus.DONE)
        )


def _live_processing(tenant: CreatedTenant) -> list[str]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return sorted(
            key
            for key in s.scalars(
                select(JobRow.key).where(
                    JobRow.kind == PROCESS_KIND,
                    JobRow.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
                )
            )
            if key is not None
        )


def _rows(tenant: CreatedTenant, party_id: UUID) -> list[Transaction]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return list(
            s.scalars(
                select(Transaction)
                .where(Transaction.party_id == party_id)
                .order_by(Transaction.occurred_at)
            )
        )


def test_a_received_row_older_than_thirty_seconds_gets_its_party_enqueued(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    customer_b: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    # The batch's enqueue was dropped while a running job passed its last read.
    _ = _ingest(tenant, queue, monkeypatch, [_item(1, DAY)], now=RECEIVED)
    fresh = RECEIVED + timedelta(seconds=50)
    _ = _ingest(
        tenant,
        queue,
        monkeypatch,
        [_item(2, DAY, customer_ref="cust-2")],
        now=fresh,
    )
    _settle_processing(tenant)
    now = RECEIVED + timedelta(minutes=1)
    report = _sweep(tenant, queue, monkeypatch, now=now)
    assert report == SweepReport(
        enqueued=1, unparked=0, policy_retried=0, notified=False
    )
    assert _live_processing(tenant) == [process_key(customer.party_id)]
    # A pending job is there already: the queue's dedup drops a second one.
    again = _sweep(tenant, queue, monkeypatch, now=now + timedelta(seconds=10))
    assert again.enqueued == 0
    # Customer B's row ages past thirty seconds a minute later.
    later = _sweep(tenant, queue, monkeypatch, now=now + timedelta(minutes=1))
    assert later.enqueued == 1
    assert _live_processing(tenant) == sorted([
        process_key(customer.party_id),
        process_key(customer_b.party_id),
    ])


def test_a_party_whose_job_went_dead_mid_drain_is_resumed_by_the_sweep(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    services: PlatformServices,
    monkeypatch: pytest.MonkeyPatch,
):
    # Plan 4a, note from A8: the reaper dead-lettered the job; nobody owns it.
    _ = _ingest(
        tenant,
        queue,
        monkeypatch,
        [_item(n, DAY + timedelta(hours=n)) for n in (1, 2)],
        now=RECEIVED,
    )
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = s.execute(
            update(JobRow)
            .where(JobRow.kind == PROCESS_KIND)
            .values(status=JobStatus.DEAD)
        )
    report = _sweep(tenant, queue, monkeypatch, now=RECEIVED + timedelta(minutes=1))
    assert report.enqueued == 1
    assert run_processing(services, tenant.tenant_id) >= 1
    assert {row.status for row in _rows(tenant, customer.party_id)} == {
        TransactionStatus.PROCESSED
    }


def test_a_party_stopped_at_a_parked_row_is_not_enqueued_every_minute(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    received = MISSING + timedelta(hours=1)
    _ = _ingest(
        tenant,
        queue,
        monkeypatch,
        [_item(1, MISSING), _item(2, MISSING + timedelta(minutes=5))],
        now=received,
    )
    report = _drain(tenant, customer.party_id, queue, monkeypatch, now=received)
    assert report.parked == 1
    _settle_processing(tenant)
    swept = _sweep(tenant, queue, monkeypatch, now=received + timedelta(minutes=5))
    assert swept.enqueued == 0
    assert _live_processing(tenant) == []


def _buckets(tenant: CreatedTenant, party_id: UUID) -> list[tuple[object, ...]]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return [
            (b.agg_id, b.bucket_start, b.bucket_kind, b.value, b.count, b.first_at)
            for b in s.scalars(
                select(AggBucket)
                .where(AggBucket.key == str(party_id))
                .order_by(AggBucket.agg_id, AggBucket.bucket_start)
            )
        ]


def _load_values_of_the_23rd(admin_engine: Engine) -> None:
    # The fixture has no value on or after the 22nd; the 23rd gets both.
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        for code, on, value in (
            ("UDI", date(2026, 1, 23), "8.640000"),
            ("USD_MXN_FIX", date(2026, 1, 22), "17.900000"),
        ):
            _ = upsert_value(
                s,
                UnitValue(
                    unit_code=code,
                    valid_from=on,
                    valid_to=on,
                    value=Decimal(value),
                    quote_ccy="MXN",
                    source="fixture",
                    confidence=Confidence.VERIFIED,
                ),
                fetched_at=datetime(2026, 1, 23, tzinfo=UTC),
            )


def test_an_unparked_row_processes_before_the_rows_behind_it(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    customer_b: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    admin_engine: Engine,
    services: PlatformServices,
    monkeypatch: pytest.MonkeyPatch,
):
    # Review Focus 3: a row parks on a missing UDI; two later rows, of a day whose
    # UDI exists, wait behind it; the value arrives, the sweep un-parks, and the
    # drain takes the three in order.
    _load_values_of_the_23rd(admin_engine)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = declare(s, CASH_SUM_P7D, queue=queue)
    later = datetime(2026, 1, 23, 12, tzinfo=UTC)
    received = later + timedelta(hours=3)
    (parked_id,) = _ingest(
        tenant, queue, monkeypatch, [_item(1, MISSING)], now=received
    )
    first = _drain(tenant, customer.party_id, queue, monkeypatch, now=received)
    assert first == ProcessReport(processed=0, parked=1, stopped_at=parked_id)
    behind = [_item(n, later + timedelta(hours=n - 2)) for n in (2, 3)]
    _ = _ingest(tenant, queue, monkeypatch, behind, now=received)
    blocked = _drain(tenant, customer.party_id, queue, monkeypatch, now=received)
    assert blocked == ProcessReport(processed=0, parked=0, stopped_at=parked_id)
    assert [row.status for row in _rows(tenant, customer.party_id)] == [
        TransactionStatus.PARKED,
        TransactionStatus.RECEIVED,
        TransactionStatus.RECEIVED,
    ]
    _settle_processing(tenant)
    # Nothing moves while the value is missing.
    idle = _sweep(tenant, queue, monkeypatch, now=received + timedelta(minutes=1))
    assert (idle.enqueued, idle.unparked) == (0, 0)
    load_missing_values(admin_engine)
    swept = _sweep(tenant, queue, monkeypatch, now=received + timedelta(minutes=2))
    assert (swept.enqueued, swept.unparked) == (1, 1)
    assert _rows(tenant, customer.party_id)[0].status is TransactionStatus.RECEIVED
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        # The parked entry stays, so parking the row again counts the attempt.
        parked = s.scalars(select(TransactionParked)).one()
        assert parked.attempts == 1
    assert run_processing(services, tenant.tenant_id) >= 1
    rows = _rows(tenant, customer.party_id)
    assert [row.external_id for row in rows] == ["tx-1", "tx-2", "tx-3"]
    assert {row.status for row in rows} == {TransactionStatus.PROCESSED}
    stamps = [row.processed_at for row in rows if row.processed_at is not None]
    assert len(stamps) == 3
    assert stamps == sorted(stamps)
    assert not any(row.late for row in rows)
    # The same stream for a party that never parked gives the same buckets.
    twin = [
        _item(1, MISSING, customer_ref="cust-2") | {"external_id": "tx-b1"},
        *(
            _item(n, later + timedelta(hours=n - 2), customer_ref="cust-2")
            | {"external_id": f"tx-b{n}"}
            for n in (2, 3)
        ),
    ]
    _ = _ingest(tenant, queue, monkeypatch, twin, now=received)
    assert run_processing(services, tenant.tenant_id) >= 1
    assert _buckets(tenant, customer.party_id) == _buckets(tenant, customer_b.party_id)
    assert len(_buckets(tenant, customer.party_id)) == 3


def _assign_again(tenant: CreatedTenant) -> None:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        version_id = s.scalars(select(TenantPackAssignment.pack_version_id)).first()
    assert version_id is not None
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = assign_pack(
            s,
            pack_version_id=version_id,
            product=None,
            effective_from=date(2026, 2, 1),
            approved_by=tenant.admin_user_id,
        )


def test_policy_not_assigned_rows_retry_hourly_or_when_a_pack_is_assigned(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    # 31 December in Mexico City: before the pack's 2026-01-01.
    before = datetime(2026, 1, 1, 2, tzinfo=UTC)
    (row_id,) = _ingest(
        tenant, queue, monkeypatch, [_item(1, before)], now=before + timedelta(hours=1)
    )
    # Parked just after the fixture's assignment was made, on the database clock.
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assigned_at = s.scalar(select(func.max(TenantPackAssignment.created_at)))
    assert assigned_at is not None
    parked_at = assigned_at + timedelta(milliseconds=1)
    report = _drain(tenant, customer.party_id, queue, monkeypatch, now=parked_at)
    assert report.parked == 1
    _settle_processing(tenant)
    early = _sweep(tenant, queue, monkeypatch, now=parked_at + timedelta(minutes=30))
    assert early.policy_retried == 0
    hourly = _sweep(tenant, queue, monkeypatch, now=parked_at + timedelta(minutes=61))
    assert (hourly.policy_retried, hourly.enqueued) == (1, 1)
    assert _rows(tenant, customer.party_id)[0].status is TransactionStatus.RECEIVED
    # The retry parks it again: one more attempt, a new parked_at.
    _settle_processing(tenant)
    reparked_at = parked_at + timedelta(milliseconds=1)
    _ = _drain(tenant, customer.party_id, queue, monkeypatch, now=reparked_at)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        parked = s.scalars(select(TransactionParked)).one()
        assert (parked.transaction_id, parked.reason, parked.attempts) == (
            row_id,
            ParkReason.POLICY_NOT_ASSIGNED,
            2,
        )
        assert parked.parked_at == reparked_at
    waiting = _sweep(tenant, queue, monkeypatch, now=reparked_at + timedelta(minutes=5))
    assert waiting.policy_retried == 0
    # An assignment created after the row parked retries it at once (Deviation 10).
    _settle_processing(tenant)
    _assign_again(tenant)
    assigned = _sweep(
        tenant, queue, monkeypatch, now=reparked_at + timedelta(minutes=6)
    )
    assert (assigned.policy_retried, assigned.enqueued) == (1, 1)


def _notices(tenant: CreatedTenant) -> list[Notification]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return list(
            s.scalars(
                select(Notification)
                .where(Notification.template == PARKED_TEMPLATE)
                .order_by(Notification.created_at, Notification.recipient_role)
            )
        )


def test_parked_over_six_hours_notifies_admin_and_mlro_once_a_day(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    # Received at 13:00 in Mexico City on the 22nd.
    received = MISSING + timedelta(hours=1)
    _ = _ingest(tenant, queue, monkeypatch, [_item(1, MISSING)], now=received)
    parked_at = received + timedelta(minutes=1)
    _ = _drain(tenant, customer.party_id, queue, monkeypatch, now=parked_at)
    soon = _sweep(tenant, queue, monkeypatch, now=received + timedelta(hours=5))
    assert soon.notified is False
    first = _sweep(tenant, queue, monkeypatch, now=received + timedelta(hours=7))
    assert first.notified is True
    second = _sweep(tenant, queue, monkeypatch, now=received + timedelta(hours=8))
    assert second.notified is False
    notices = _notices(tenant)
    assert sorted(notice.recipient_role or "" for notice in notices) == [
        "ADMIN",
        "MLRO",
    ]
    assert all(
        notice.data == {"parked_count": 1, "oldest_parked_at": parked_at.isoformat()}
        for notice in notices
    )
    # 07:00 UTC on the 23rd is 01:00 in Mexico City: a new day.
    next_day = _sweep(
        tenant, queue, monkeypatch, now=datetime(2026, 1, 23, 7, tzinfo=UTC)
    )
    assert next_day.notified is True
    assert len(_notices(tenant)) == 4


def test_a_policy_row_parked_seven_times_notifies_though_its_parked_at_is_new(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    before = datetime(2026, 1, 1, 2, tzinfo=UTC)
    _ = _ingest(
        tenant, queue, monkeypatch, [_item(1, before)], now=before + timedelta(hours=1)
    )
    # Parked after the fixture's assignment, so the sweep does not retry it.
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assigned_at = s.scalar(select(func.max(TenantPackAssignment.created_at)))
    assert assigned_at is not None
    parked_at = assigned_at + timedelta(milliseconds=1)
    assert _drain(tenant, customer.party_id, queue, monkeypatch, now=parked_at).parked

    def parked_times(attempts: int) -> None:
        with tenant_session(system_ctx(tenant.tenant_id)) as s:
            _ = s.execute(update(TransactionParked).values(attempts=attempts))

    # Each hourly retry parks it again with a new parked_at: the count ages it.
    parked_times(6)
    soon = _sweep(tenant, queue, monkeypatch, now=parked_at + timedelta(minutes=5))
    assert soon.notified is False
    parked_times(7)
    told = _sweep(tenant, queue, monkeypatch, now=parked_at + timedelta(minutes=6))
    assert told.notified is True
    assert all(
        notice.data == {"parked_count": 1, "oldest_parked_at": parked_at.isoformat()}
        for notice in _notices(tenant)
    )


def test_the_sweep_job_runs_through_the_runner_on_its_schedule(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    services: PlatformServices,
    monkeypatch: pytest.MonkeyPatch,
):
    # Received a minute ago on the real clock, which the scheduled job reads.
    now = clock.now()
    _ = _ingest(
        tenant,
        queue,
        monkeypatch,
        [_item(1, now - timedelta(minutes=2))],
        now=now - timedelta(minutes=1),
    )
    _settle_processing(tenant)
    factory = get_sessionmaker()
    scheduler = Scheduler(
        queue, factory, clock_fn=lambda: clock.now() + timedelta(minutes=2)
    )
    assert scheduler.tick() >= 1
    runner = JobRunner(queue, factory, worker_id="w-sweep", kinds=[SWEEP_KIND])
    assert runner.run_once() == 1
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        swept = s.scalars(select(JobRow.status).where(JobRow.kind == SWEEP_KIND)).all()
    assert list(swept) == [JobStatus.DONE]
    assert _live_processing(tenant) == [process_key(customer.party_id)]


def test_an_incomplete_declaration_with_no_live_backfill_is_enqueued_again(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
):
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        (agg_id,) = ensure_declarations(s, [CASH_SUM_P7D], queue=queue)
    report = _sweep(tenant, queue, monkeypatch, now=clock.now())
    # Its first backfill is still pending.
    assert report.backfills == 0
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = s.execute(
            update(JobRow)
            .where(JobRow.kind == AGG_BACKFILL_KIND)
            .values(status=JobStatus.DEAD)
        )
    report = _sweep(tenant, queue, monkeypatch, now=clock.now())
    assert report.backfills == 1
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        live = s.scalars(
            select(JobRow.key).where(
                JobRow.kind == AGG_BACKFILL_KIND, JobRow.status == JobStatus.PENDING
            )
        ).all()
    assert list(live) == [f"agg_backfill:{agg_id}"]


def _backfill_jobs(tenant: CreatedTenant) -> list[JobRow]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return list(
            s.scalars(
                select(JobRow)
                .where(JobRow.kind == AGG_BACKFILL_KIND)
                .order_by(JobRow.created_at)
            )
        )


def _kill_backfills(tenant: CreatedTenant, *, at: datetime) -> None:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = s.execute(
            update(JobRow)
            .where(
                JobRow.kind == AGG_BACKFILL_KIND,
                JobRow.status == JobStatus.PENDING,
            )
            # The queue stamps created_at from its own clock: move it with ``at``.
            .values(
                status=JobStatus.DEAD,
                created_at=at - timedelta(minutes=1),
                finished_at=at,
            )
        )


def test_a_backfill_that_keeps_dying_is_retried_hourly_and_logged_daily(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    # 18:00 in Mexico City on the 22nd.
    died = datetime(2026, 1, 23, tzinfo=UTC)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        (agg_id,) = ensure_declarations(s, [CASH_SUM_P7D], queue=queue)
    _kill_backfills(tenant, at=died)
    # Within the hour after it died, the sweep leaves it.
    soon = _sweep(tenant, queue, monkeypatch, now=died + timedelta(minutes=30))
    assert soon.backfills == 0
    with caplog.at_level(logging.WARNING):
        retried = _sweep(tenant, queue, monkeypatch, now=died + timedelta(minutes=61))
    assert retried.backfills == 1
    # One death is not yet worth a warning.
    assert not [r for r in caplog.records if "keeps dying" in r.message]
    _kill_backfills(tenant, at=died + timedelta(minutes=62))
    with caplog.at_level(logging.WARNING):
        again = _sweep(tenant, queue, monkeypatch, now=died + timedelta(hours=2))
        assert again.backfills == 0
        # Two deaths: the first enqueue of the next Mexico City day (01:00 on the
        # 23rd) warns, with ids only; later ones that day do not.
        third = _sweep(tenant, queue, monkeypatch, now=died + timedelta(hours=7))
        assert third.backfills == 1
        _kill_backfills(tenant, at=died + timedelta(hours=7))
        fourth = _sweep(tenant, queue, monkeypatch, now=died + timedelta(hours=9))
        assert fourth.backfills == 1
    warnings = [r for r in caplog.records if "keeps dying" in r.message]
    assert [
        (getattr(r, "agg_id", None), getattr(r, "deaths", None)) for r in warnings
    ] == [(agg_id, 2)]


def test_a_deferred_backfills_follow_up_counts_as_its_live_job(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
):
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        (agg_id,) = ensure_declarations(s, [CASH_SUM_P7D], queue=queue)
    _kill_backfills(tenant, at=clock.now() - timedelta(hours=2))
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = queue.enqueue(
            s,
            kind=AGG_BACKFILL_KIND,
            payload={"agg_id": agg_id, "deferrals": 1},
            key=f"agg_backfill:{agg_id}:after:x",
        )
    assert _sweep(tenant, queue, monkeypatch, now=clock.now()).backfills == 0
    assert len(_backfill_jobs(tenant)) == 2


def test_a_file_left_processing_with_no_live_job_is_reported(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        file = TransactionFile(
            tenant_id=tenant.tenant_id,
            storage_key="transactions/files/x",
            sha256=b"\x01" * 32,
            size=10,
            content_type="text/csv",
            status=FileStatus.PROCESSING,
            uploaded_by="cli:test",
        )
        s.add(file)
        s.flush()
        file_id = file.id
    with caplog.at_level(logging.WARNING):
        report = _sweep(tenant, queue, monkeypatch, now=clock.now())
    assert report.stranded_files == 1
    (warning,) = [r for r in caplog.records if "no live ingest job" in r.message]
    assert getattr(warning, "file_ids", None) == [str(file_id)]
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = queue.enqueue(
            s,
            kind="monitoring.ingest_file",
            key=f"ingest_file:{file_id}",
            payload={"file_id": str(file_id)},
        )
    assert _sweep(tenant, queue, monkeypatch, now=clock.now()).stranded_files == 0
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert s.scalar(select(func.count()).select_from(TransactionFile)) == 1
