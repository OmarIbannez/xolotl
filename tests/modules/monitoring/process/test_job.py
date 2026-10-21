"""The per-party processing job: order, one transaction per row, parking (A8)."""

import threading
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.orm import Session

from tests.helpers import staff_session, system_ctx
from tests.modules.monitoring.support import (
    add_customer,
    declare,
    ingest_items,
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
from xolotl.modules.monitoring.aggregates.models import (
    AggBucket,
    AggDeclaration,
    AggDistinct,
)
from xolotl.modules.monitoring.aggregates.postgres import PostgresAggregateStore
from xolotl.modules.monitoring.aggregates.store import AggregateKey, AggregateValue
from xolotl.modules.monitoring.enrich.policy import SnapshotRef
from xolotl.modules.monitoring.enrich.service import ParkTransactionError
from xolotl.modules.monitoring.enums import ParkReason, TransactionStatus
from xolotl.modules.monitoring.events import TRANSACTION_PARKED, TRANSACTION_PROCESSED
from xolotl.modules.monitoring.kinds import AGG_BACKFILL_KIND, PROCESS_KIND
from xolotl.modules.monitoring.process.hooks import (
    ProcessingContext,
    ProcessingHook,
    processing_hooks,
    register_processing_hook,
    reset_processing_hooks,
)
from xolotl.modules.monitoring.process.job import (
    ProcessJob,
    ProcessReport,
    lock_party,
    pending_rows,
    process_party,
)
from xolotl.modules.monitoring.txn.models import Transaction, TransactionParked
from xolotl.modules.monitoring.txn.schema import (
    EnrichedTransaction,
    Instrument,
    KeyKind,
)
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import Settings, get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus
from xolotl.platform.events import is_staff_only
from xolotl.platform.events.event import Event, event_from_row
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import Job, PostgresJobQueue, register_job_handler
from xolotl.platform.jobs.runner import JobRunner
from xolotl.platform.services import PlatformServices

# Every unit value the ITF pack reads exists for 2026-01-20 in Mexico City.
DAY = datetime(2026, 1, 20, 12, 0, tzinfo=UTC)
# The evening of the 20th in Mexico City: nothing is late at receipt.
RECEIVED = datetime(2026, 1, 21, 5, 0, tzinfo=UTC)

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
COUNTERPARTY = {"kind": "PERSON", "name": "Juan Pérez", "country": "MX"}
HOOK_COUNT_P7D = AggregateDeclaration(
    name="hook_count_p7d",
    function=AggregateFunction.COUNT,
    measure=Measure.ONE,
    unit_key=None,
    key_kind=KeyKind.PARTY,
    window_kind=WindowKind.DURATION,
    window="P7D",
)


@pytest.fixture
def hooks() -> Iterator[None]:
    """Start and end with no processing hook registered.

    Yields:
        Nothing.

    """
    reset_processing_hooks()
    yield
    reset_processing_hooks()


@pytest.fixture
def services(engine: Engine) -> PlatformServices:
    return wire_platform(get_sessionmaker(), get_settings())


@pytest.fixture
def customer_b(tenant: CreatedTenant) -> CustomerRelationship:
    return add_customer(tenant, "cust-2")


@pytest.fixture
def declared(
    tenant: CreatedTenant, itf_tenant: UUID, queue: PostgresJobQueue
) -> AggregateDeclaration:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = declare(s, CASH_SUM_P7D, queue=queue)
    return CASH_SUM_P7D


def _ingest(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
    items: Sequence[dict[str, object]],
    *,
    now: datetime = RECEIVED,
) -> list[UUID]:
    """Ingest ``items`` as if received at ``now``; processing keeps the real clock.

    Returns:
        The ids of the rows, in the order of ``items``.

    """
    with monkeypatch.context() as patched:
        patched.setattr(clock, "now", lambda: now)
        with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
            report = ingest_items(s, items, queue=queue)
    return list(report.transaction_ids)


def _item(number: int, at: datetime, **overrides: object) -> dict[str, object]:
    return make_item(
        external_id=f"tx-{number}",
        occurred_at=at.isoformat(),
        amount=f"{number}00.00",
        **overrides,
    )


def _rows(tenant: CreatedTenant) -> list[Transaction]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return list(
            s.scalars(select(Transaction).order_by(Transaction.occurred_at)).all()
        )


def _drain(
    tenant: CreatedTenant,
    party_id: UUID,
    queue: PostgresJobQueue,
    *,
    job: Job | None = None,
    settings: Settings | None = None,
) -> ProcessReport:
    return process_party(
        get_sessionmaker(),
        tenant.tenant_id,
        party_id,
        queue=queue,
        job=job,
        settings=settings or get_settings(),
        actor="cli:test",
    )


def _read(
    tenant: CreatedTenant,
    party_id: UUID,
    declaration: AggregateDeclaration = CASH_SUM_P7D,
    *,
    at: datetime = RECEIVED,
) -> AggregateValue:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return PostgresAggregateStore(s).read(
            AggregateKey(KeyKind.PARTY, str(party_id)), declaration, at=at
        )


def _events(tenant: CreatedTenant, event_type: str) -> list[Event]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        rows = s.scalars(
            select(OutboxRow)
            .where(OutboxRow.type == event_type)
            .order_by(OutboxRow.seq)
        ).all()
        return [event_from_row(row) for row in rows]


def _a_job(tenant: CreatedTenant, party_id: UUID) -> Job:
    return Job(
        id=uuid7(),
        tenant_id=tenant.tenant_id,
        kind=PROCESS_KIND,
        key=f"process:{party_id}",
        payload={"party_id": str(party_id)},
        attempts=1,
        max_attempts=1,
        lock_token=uuid7(),
    )


@dataclass
class RecordingHook:
    """A hook that records what it sees, and may raise on one external id."""

    name: str
    seen: list[tuple[str, str]]
    raise_on: str | None = None
    asked: list[UUID] = field(default_factory=list)
    reads: list[tuple[int, int, bool, bool]] = field(default_factory=list)

    def declarations(
        self, s: Session, snapshot: SnapshotRef, *, on: date, product: str | None
    ) -> Sequence[AggregateDeclaration]:
        _ = (s, on, product)
        self.asked.append(snapshot.snapshot_id)
        return [HOOK_COUNT_P7D]

    def evaluate(
        self, s: Session, txn: EnrichedTransaction, ctx: ProcessingContext
    ) -> None:
        if txn.external_id == self.raise_on:
            msg = "the hook failed"
            raise RuntimeError(msg)
        self.seen.append((self.name, txn.external_id))
        key = AggregateKey(KeyKind.PARTY, str(txn.party_id))
        cash = ctx.store.read(key, CASH_SUM_P7D, at=txn.occurred_at)
        hooked = ctx.store.read(key, HOOK_COUNT_P7D, at=txn.occurred_at)
        status = s.scalar(select(Transaction.status).where(Transaction.id == txn.id))
        self.reads.append((
            cash.count,
            hooked.count,
            hooked.complete,
            status is TransactionStatus.RECEIVED,
        ))


def test_a_drain_processes_in_occurred_at_order_and_updates_the_declared_buckets(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    declared: AggregateDeclaration,
    services: PlatformServices,
    monkeypatch: pytest.MonkeyPatch,
):
    items = [
        _item(2, DAY + timedelta(hours=2)),
        _item(1, DAY),
        _item(3, DAY + timedelta(hours=4)),
    ]
    _ = _ingest(tenant, queue, monkeypatch, items)
    assert run_processing(services, tenant.tenant_id) >= 1
    rows = _rows(tenant)
    assert [row.external_id for row in rows] == ["tx-1", "tx-2", "tx-3"]
    assert {row.status for row in rows} == {TransactionStatus.PROCESSED}
    processed = [row.processed_at for row in rows if row.processed_at is not None]
    assert len(processed) == 3
    assert processed == sorted(processed)
    assert all(row.policy_snapshot_id is not None for row in rows)
    assert all(row.unit_amounts for row in rows)
    read = _read(tenant, customer.party_id, declared)
    assert (read.value, read.count, read.complete) == (60_000, 3, True)


def test_a_failure_after_add_rolls_the_buckets_back_with_the_row(
    hooks: None,
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    declared: AggregateDeclaration,
    monkeypatch: pytest.MonkeyPatch,
):
    # Review Focus 2.
    _ = _ingest(
        tenant,
        queue,
        monkeypatch,
        [_item(n, DAY + timedelta(hours=n)) for n in (1, 2, 3)],
    )
    seen: list[tuple[str, str]] = []
    register_processing_hook(RecordingHook("failing", seen, raise_on="tx-2"))
    with pytest.raises(RuntimeError, match="the hook failed"):
        _ = _drain(tenant, customer.party_id, queue)
    rows = _rows(tenant)
    assert [row.status for row in rows] == [
        TransactionStatus.PROCESSED,
        TransactionStatus.RECEIVED,
        TransactionStatus.RECEIVED,
    ]
    assert rows[1].unit_amounts is None
    assert rows[1].processed_at is None
    assert _read(tenant, customer.party_id).count == 1
    register_processing_hook(RecordingHook("failing", seen))
    report = _drain(tenant, customer.party_id, queue)
    assert report == ProcessReport(processed=2, parked=0, stopped_at=None)
    read = _read(tenant, customer.party_id)
    assert (read.count, read.value) == (3, 60_000)
    assert seen == [("failing", "tx-1"), ("failing", "tx-2"), ("failing", "tx-3")]


def test_a_late_row_is_flagged_and_an_out_of_order_row_too(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    on_time = _item(1, DAY + timedelta(hours=6))
    received_late = _item(2, DAY + timedelta(hours=1))
    _ = _ingest(tenant, queue, monkeypatch, [on_time])
    _ = _drain(tenant, customer.party_id, queue)
    # Received two days after it occurred, and before the row processed above.
    _ = _ingest(
        tenant, queue, monkeypatch, [received_late], now=DAY + timedelta(days=2)
    )
    # Received in time but older than the latest processed row.
    out_of_order = _item(3, DAY + timedelta(hours=3))
    _ = _ingest(tenant, queue, monkeypatch, [out_of_order])
    _ = _drain(tenant, customer.party_id, queue)
    late = {row.external_id: row.late for row in _rows(tenant)}
    assert late == {"tx-1": False, "tx-2": True, "tx-3": True}


def test_a_row_received_over_a_day_late_is_late_with_nothing_processed_before(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    _ = _ingest(
        tenant, queue, monkeypatch, [_item(1, DAY)], now=DAY + timedelta(days=3)
    )
    _ = _drain(tenant, customer.party_id, queue)
    (row,) = _rows(tenant)
    assert (row.status, row.late) == (TransactionStatus.PROCESSED, True)


def test_a_missing_unit_value_parks_the_row_and_stops_the_key(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    declared: AggregateDeclaration,
    services: PlatformServices,
    monkeypatch: pytest.MonkeyPatch,
):
    # 2026-01-22 has no UDI value in the fixture.
    missing = datetime(2026, 1, 22, 18, tzinfo=UTC)
    ids = _ingest(
        tenant,
        queue,
        monkeypatch,
        [
            _item(1, DAY),
            _item(2, missing),
            _item(3, missing + timedelta(hours=1)),
        ],
        now=missing + timedelta(hours=2),
    )
    report = _drain(tenant, customer.party_id, queue)
    assert report == ProcessReport(processed=1, parked=1, stopped_at=ids[1])
    assert [row.status for row in _rows(tenant)] == [
        TransactionStatus.PROCESSED,
        TransactionStatus.PARKED,
        TransactionStatus.RECEIVED,
    ]
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        parked = s.scalars(select(TransactionParked)).one()
        assert (parked.transaction_id, parked.reason) == (
            ids[1],
            ParkReason.UNIT_VALUE_MISSING,
        )
        assert (parked.unit_code, parked.needed_on, parked.attempts) == (
            "UDI",
            date(2026, 1, 22),
            1,
        )
        assert parked.last_error == "no UDI value for 2026-01-22"
    assert _read(tenant, customer.party_id, at=missing).count == 1
    # A parked row stops every later drain at it, with nothing parked again.
    again = _drain(tenant, customer.party_id, queue)
    assert again == ProcessReport(processed=0, parked=0, stopped_at=ids[1])
    (event,) = _events(tenant, TRANSACTION_PARKED)
    assert event.data == {
        "transaction_id": str(ids[1]),
        "reason": "UNIT_VALUE_MISSING",
        "unit_code": "UDI",
        "needed_on": "2026-01-22",
    }
    assert event.key == str(customer.party_id)
    assert event.subject == f"transaction/{ids[1]}"
    assert is_staff_only(TRANSACTION_PARKED)


def test_parking_a_row_again_counts_the_attempt(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    missing = datetime(2026, 1, 22, 18, tzinfo=UTC)
    (row_id,) = _ingest(
        tenant,
        queue,
        monkeypatch,
        [_item(1, missing)],
        now=missing + timedelta(hours=1),
    )
    _ = _drain(tenant, customer.party_id, queue)
    # What the sweep (A9) does once it retries the row.
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = s.execute(
            update(Transaction)
            .where(Transaction.id == row_id)
            .values(status=TransactionStatus.RECEIVED)
        )
    _ = _drain(tenant, customer.party_id, queue)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        parked = s.scalars(select(TransactionParked)).one()
        assert parked.attempts == 2
    assert len(_events(tenant, TRANSACTION_PARKED)) == 2


def test_no_policy_on_the_date_parks_with_policy_not_assigned(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    # The pack is assigned from 2026-01-01; this is 31 December in Mexico City.
    before = datetime(2026, 1, 1, 2, tzinfo=UTC)
    (row_id,) = _ingest(
        tenant, queue, monkeypatch, [_item(1, before)], now=before + timedelta(hours=1)
    )
    report = _drain(tenant, customer.party_id, queue)
    assert report == ProcessReport(processed=0, parked=1, stopped_at=row_id)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        parked = s.scalars(select(TransactionParked)).one()
        assert (parked.reason, parked.unit_code, parked.needed_on) == (
            ParkReason.POLICY_NOT_ASSIGNED,
            None,
            None,
        )
    (event,) = _events(tenant, TRANSACTION_PARKED)
    assert event.data == {
        "transaction_id": str(row_id),
        "reason": "POLICY_NOT_ASSIGNED",
        "unit_code": None,
        "needed_on": None,
    }


@dataclass
class _Parking:
    """A hook that parks the row after its aggregates were added."""

    name: str = "parking"
    calls: list[str] = field(default_factory=list)

    def declarations(
        self, s: Session, snapshot: SnapshotRef, *, on: date, product: str | None
    ) -> Sequence[AggregateDeclaration]:
        _ = (s, snapshot, on, product)
        self.calls.append("declarations")
        return ()

    def evaluate(
        self, s: Session, txn: EnrichedTransaction, ctx: ProcessingContext
    ) -> None:
        _ = (s, txn, ctx)
        self.calls.append("evaluate")
        raise ParkTransactionError(
            ParkReason.UNIT_VALUE_MISSING, "UMA", needed_on=date(2026, 1, 20)
        )


def test_a_park_raised_after_add_takes_the_rows_aggregates_back(
    hooks: None,
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    declared: AggregateDeclaration,
    monkeypatch: pytest.MonkeyPatch,
):
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = declare(s, COUNTERPARTIES_P7D, queue=queue)
    hook: ProcessingHook = _Parking()
    register_processing_hook(hook)
    (row_id,) = _ingest(
        tenant, queue, monkeypatch, [_item(1, DAY, counterparty=COUNTERPARTY)]
    )
    report = _drain(tenant, customer.party_id, queue)
    assert report == ProcessReport(processed=0, parked=1, stopped_at=row_id)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert s.scalar(select(func.count()).select_from(AggBucket)) == 0
        assert s.scalar(select(func.count()).select_from(AggDistinct)) == 0
    (row,) = _rows(tenant)
    assert (row.status, row.unit_amounts, row.processed_at) == (
        TransactionStatus.PARKED,
        None,
        None,
    )
    assert _read(tenant, customer.party_id).count == 0
    assert _events(tenant, TRANSACTION_PROCESSED) == []


def test_hooks_run_in_registration_order_inside_the_row_transaction(
    hooks: None,
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    declared: AggregateDeclaration,
    monkeypatch: pytest.MonkeyPatch,
):
    seen: list[tuple[str, str]] = []
    first = RecordingHook("first", seen)
    second = RecordingHook("second", seen)
    register_processing_hook(first)
    register_processing_hook(second)
    # Registering a name again replaces the hook in its place.
    register_processing_hook(first)
    assert [hook.name for hook in processing_hooks()] == ["first", "second"]
    _ = _ingest(
        tenant, queue, monkeypatch, [_item(n, DAY + timedelta(hours=n)) for n in (1, 2)]
    )
    report = _drain(tenant, customer.party_id, queue)
    assert report.processed == 2
    assert seen == [
        ("first", "tx-1"),
        ("second", "tx-1"),
        ("first", "tx-2"),
        ("second", "tx-2"),
    ]
    # The reads include the row under evaluation, the row is not yet PROCESSED,
    # and the hooks' new declaration reads INCOMPLETE until its backfill runs.
    assert first.reads == [(1, 1, False, True), (2, 2, False, True)]
    # One snapshot in this drain: each hook was asked once.
    assert len(first.asked) == len(second.asked) == 1
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        stored = s.get_one(AggDeclaration, (tenant.tenant_id, HOOK_COUNT_P7D.agg_id()))
        assert stored.backfilled_through is None
        backfills = s.scalars(
            select(JobRow.key).where(
                JobRow.kind == AGG_BACKFILL_KIND,
                JobRow.key == f"agg_backfill:{HOOK_COUNT_P7D.agg_id()}",
            )
        ).all()
    assert len(backfills) == 1


def test_the_processed_event_carries_ids_and_status_only(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    services: PlatformServices,
    monkeypatch: pytest.MonkeyPatch,
):
    (row_id,) = _ingest(tenant, queue, monkeypatch, [_item(1, DAY)])
    _ = _drain(tenant, customer.party_id, queue)
    (event,) = _events(tenant, TRANSACTION_PROCESSED)
    assert event.data == {
        "transaction_id": str(row_id),
        "external_id": "tx-1",
        "status": "PROCESSED",
    }
    assert event.key == str(customer.party_id)
    assert event.source == "xolotl.monitoring"
    assert event.subject == f"transaction/{row_id}"
    assert event.policy_snapshot_id == _rows(tenant)[0].policy_snapshot_id
    assert is_staff_only(TRANSACTION_PROCESSED)


def test_a_drain_leaves_other_parties_alone(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    customer_b: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    _ = _ingest(
        tenant,
        queue,
        monkeypatch,
        [_item(1, DAY), _item(2, DAY, customer_ref="cust-2")],
    )
    assert _drain(tenant, customer.party_id, queue).processed == 1
    status = {row.external_id: row.status for row in _rows(tenant)}
    assert status == {
        "tx-1": TransactionStatus.PROCESSED,
        "tx-2": TransactionStatus.RECEIVED,
    }


def test_a_page_heartbeats_the_job(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    _ = _ingest(
        tenant,
        queue,
        monkeypatch,
        [_item(n, DAY + timedelta(minutes=n)) for n in range(1, 6)],
    )
    beats: list[timedelta] = []

    def heartbeat(job: Job, *, lease: timedelta) -> bool:
        _ = job
        beats.append(lease)
        return True

    monkeypatch.setattr(queue, "heartbeat", heartbeat)
    settings = get_settings().model_copy(update={"monitoring_process_page": 2})
    report = _drain(
        tenant,
        customer.party_id,
        queue,
        job=_a_job(tenant, customer.party_id),
        settings=settings,
    )
    assert report.processed == 5
    assert len(beats) >= 2
    lease = timedelta(seconds=settings.monitoring_process_lease_seconds)
    assert set(beats) == {lease}


def test_a_drain_whose_heartbeat_is_refused_stops_after_the_page(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    _ = _ingest(
        tenant,
        queue,
        monkeypatch,
        [_item(n, DAY + timedelta(minutes=n)) for n in range(1, 6)],
    )

    def lost(job: Job, *, lease: timedelta) -> bool:
        _ = (job, lease)
        return False

    monkeypatch.setattr(queue, "heartbeat", lost)
    settings = get_settings().model_copy(update={"monitoring_process_page": 2})
    report = _drain(
        tenant,
        customer.party_id,
        queue,
        job=_a_job(tenant, customer.party_id),
        settings=settings,
    )
    # Another run owns the job now; it takes the rest from here.
    assert report == ProcessReport(processed=2, parked=0, stopped_at=None)
    assert [row.status for row in _rows(tenant)].count(TransactionStatus.RECEIVED) == 3


def _until(condition: Callable[[], bool], what: str, timeout: float = 10) -> None:
    """Poll ``condition`` until it holds.

    Raises:
        AssertionError: When it does not hold within ``timeout`` seconds.

    """
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            msg = f"timed out waiting until {what}"
            raise AssertionError(msg)
        time.sleep(0.01)


_BLOCKED_BY = text(
    "SELECT count(*) FROM pg_stat_activity WHERE :pid = ANY(pg_blocking_pids(pid))"
)


def _blocked_by(admin_engine: Engine, pid: int) -> bool:
    with admin_engine.connect() as conn:
        waiting = conn.execute(_BLOCKED_BY, {"pid": pid}).scalar_one()
    return waiting > 0


@pytest.mark.parametrize(
    ("other_run", "expected"),
    [
        (TransactionStatus.PROCESSED, (1, 0, None)),
        (TransactionStatus.PARKED, (0, 0, 0)),
    ],
)
def test_a_drain_waits_for_a_row_another_run_holds_and_reads_its_status_again(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    declared: AggregateDeclaration,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    other_run: TransactionStatus,
    expected: tuple[int, int, int | None],
):
    # A run whose lease was reaped can still be draining while another runs: the
    # row lock makes the second wait, then pass over a processed row or stop at
    # a parked one, so no row is added twice and none jumps the queue.
    ids = _ingest(
        tenant, queue, monkeypatch, [_item(n, DAY + timedelta(hours=n)) for n in (1, 2)]
    )
    first = _rows(tenant)[0]
    with ThreadPoolExecutor(max_workers=1) as pool:
        with tenant_session(system_ctx(tenant.tenant_id)) as s:
            pid = s.execute(text("SELECT pg_backend_pid()")).scalar_one()
            held = s.get_one(
                Transaction,
                (tenant.tenant_id, first.occurred_at, first.id),
                with_for_update=True,
            )
            future = pool.submit(_drain, tenant, customer.party_id, queue)
            _until(lambda: _blocked_by(admin_engine, pid), "the drain waits on the row")
            held.status = other_run
            if other_run is TransactionStatus.PROCESSED:
                held.processed_at = clock.now()
        _ = wait([future], timeout=30)
    report = future.result()
    processed, parked, stopped = expected
    assert (report.processed, report.parked) == (processed, parked)
    assert report.stopped_at == (None if stopped is None else ids[stopped])
    # The other run's row is not in the buckets: it never ran the store here.
    assert _read(tenant, customer.party_id).count == processed


def test_two_runners_never_process_one_party_at_once(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    customer_b: CustomerRelationship,
    queue: PostgresJobQueue,
    declared: AggregateDeclaration,
    monkeypatch: pytest.MonkeyPatch,
):
    job = ProcessJob(
        session_factory=get_sessionmaker(), queue=queue, settings=get_settings()
    )
    register_job_handler(PROCESS_KIND, job.run)
    start = datetime(2026, 1, 20, 7, tzinfo=UTC)
    items = [_item(n, start + timedelta(minutes=n)) for n in range(1, 201)]
    items += [
        _item(n, start + timedelta(minutes=n), customer_ref="cust-2")
        for n in range(201, 211)
    ]
    # Ingested newest first, in two batches, so each party gets two enqueues.
    _ = _ingest(tenant, queue, monkeypatch, items[::-1][:100])
    _ = _ingest(tenant, queue, monkeypatch, items[::-1][100:])
    runners = [
        JobRunner(queue, get_sessionmaker(), worker_id=f"w{n}", kinds=[PROCESS_KIND])
        for n in (1, 2)
    ]
    go = threading.Barrier(2)

    def pending() -> int:
        with tenant_session(system_ctx(tenant.tenant_id)) as s:
            return (
                s.scalar(
                    select(func.count())
                    .select_from(JobRow)
                    .where(
                        JobRow.kind == PROCESS_KIND,
                        JobRow.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
                    )
                )
                or 0
            )

    def work(runner: JobRunner) -> int:
        _ = go.wait()
        ran = 0
        for _ in range(50):
            ran += runner.run_once()
            if not pending():
                break
        return ran

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(work, runner) for runner in runners]
        _ = wait(futures, timeout=120)
    assert sum(future.result() for future in futures) >= 2
    rows = _rows(tenant)
    assert {row.status for row in rows} == {TransactionStatus.PROCESSED}
    for party_id in (customer.party_id, customer_b.party_id):
        mine = [row for row in rows if row.party_id == party_id]
        stamps = [row.processed_at for row in mine]
        assert all(
            earlier is not None and later is not None and earlier < later
            for earlier, later in pairwise(stamps)
        )
    read = _read(tenant, customer.party_id)
    assert (read.count, read.value) == (200, sum(n * 10_000 for n in range(1, 201)))
    assert len(_events(tenant, TRANSACTION_PROCESSED)) == 210
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        failed = s.scalar(
            select(func.count())
            .select_from(JobRow)
            .where(JobRow.kind == PROCESS_KIND, JobRow.status != JobStatus.DONE)
        )
    assert failed == 0


def test_a_drain_waits_on_the_party_lock_and_flags_a_row_another_drain_passed(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    declared: AggregateDeclaration,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
):
    # Two drains of one party (the CLI beside the worker, or a reaped run) take
    # turns on the party's lock; the one that waited reads the party's latest
    # processed instant again, so the earlier row it processes after the other
    # drain's later one is flagged late.
    ids = _ingest(
        tenant, queue, monkeypatch, [_item(n, DAY + timedelta(hours=n)) for n in (1, 2)]
    )
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        pending = pending_rows(s, customer.party_id, limit=10)
        assert [row.id for row in pending] == ids
    second = pending[1]
    with ThreadPoolExecutor(max_workers=1) as pool:
        with tenant_session(system_ctx(tenant.tenant_id)) as s:
            pid = s.execute(text("SELECT pg_backend_pid()")).scalar_one()
            lock_party(s, tenant.tenant_id, customer.party_id)
            future = pool.submit(_drain, tenant, customer.party_id, queue)
            _until(
                lambda: _blocked_by(admin_engine, pid), "the drain waits on the party"
            )
            # Nothing of the party moved while the lock was held.
            assert {row.status for row in _rows(tenant)} == {TransactionStatus.RECEIVED}
            # The other drain processes the later row before it lets go.
            _ = s.execute(
                update(Transaction)
                .where(
                    Transaction.occurred_at == second.occurred_at,
                    Transaction.id == second.id,
                )
                .values(status=TransactionStatus.PROCESSED, processed_at=clock.now())
            )
        _ = wait([future], timeout=30)
    assert future.result() == ProcessReport(processed=1, parked=0, stopped_at=None)
    first, later = _rows(tenant)
    assert (first.status, first.late) == (TransactionStatus.PROCESSED, True)
    assert later.status is TransactionStatus.PROCESSED
