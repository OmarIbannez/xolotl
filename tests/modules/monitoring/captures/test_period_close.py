"""The period close against concurrent writers, and its bounds (B11 fix round 1).

A period's close and an event's writer lock the period row against each other:
the close waits for a writer in flight and counts its event, and a writer that
comes after the close finds the period ``CLOSED`` and reopens it. A voided extra
is published and gives back what merged into it. The check streams its rows, the
accumulation walk starts at the groups processing stored, each period closes in
its own transaction, and ``ensure_periods`` starts after the last close.
"""

import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import Engine, Select, delete, event, select, text, update
from sqlalchemy.orm import ORMExecuteState, Session

from tests.helpers import audit_events, system_ctx
from tests.modules.monitoring.captures.support import (
    UNIT_VALUES_EXTRA,
    CaptureStage,
    add_relationship,
    build_stage,
    crypto_item,
    events,
    feed,
    outbox,
    periods,
)
from tests.modules.monitoring.captures.test_periods import (
    AVI_A,
    NEW_YEAR,
    Q4,
    ROR,
    by_external_id,
    close_all,
    close_one,
    golden,
    period_of,
)
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.captures import groups as groups_module
from xolotl.modules.monitoring.captures import periods as periods_module
from xolotl.modules.monitoring.captures.definitions import capture_definitions
from xolotl.modules.monitoring.captures.enums import (
    CapturePeriodStatus,
    ReportableEventStatus,
)
from xolotl.modules.monitoring.captures.models import CapturePeriod, ReportableEvent
from xolotl.modules.monitoring.captures.periods import (
    check_captures,
    close_period_by_hand,
    definition_for,
    ensure_periods,
)
from xolotl.modules.monitoring.captures.record import (
    CaptureRun,
    reopen_touched,
    write_event,
)
from xolotl.modules.monitoring.enrich.service import enriched_from_row
from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.events import (
    CAPTURE_PERIOD_CHANGED,
    REPORTABLE_EVENT_CAPTURED,
    REPORTABLE_EVENT_SUPERSEDED,
)
from xolotl.modules.monitoring.process.hooks import reset_processing_hooks
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.calendar import load_holidays
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import HolidayScope
from xolotl.platform.policy import get_snapshot
from xolotl.platform.units import UnitService

AVI_B = "AVI_XVI_B"
LOCK_WAIT_SECONDS = 5.0


@pytest.fixture
def forget_hooks() -> Iterator[None]:
    yield
    reset_processing_hooks()


@pytest.fixture
def new_year(monkeypatch: pytest.MonkeyPatch) -> datetime:
    monkeypatch.setattr(clock, "now", lambda: NEW_YEAR)
    return NEW_YEAR


def unprocess(stage: CaptureStage, transaction_id: UUID) -> None:
    """Put a row back before its processing: not ``PROCESSED``, no event."""
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        _ = s.execute(
            update(Transaction)
            .where(Transaction.id == transaction_id)
            .values(status=TransactionStatus.RECEIVED)
        )
        _ = s.execute(
            delete(ReportableEvent).where(
                ReportableEvent.transaction_id == transaction_id
            )
        )


def process_capture(s: Session, transaction_id: UUID, code: str) -> None:
    """Do what processing does for one row: mark it and write its event."""
    row = s.scalars(select(Transaction).where(Transaction.id == transaction_id)).one()
    row.status = TransactionStatus.PROCESSED
    s.flush()
    txn = enriched_from_row(row)
    assert txn.policy_snapshot_id is not None
    policy = get_snapshot(s, txn.policy_snapshot_id).policy
    definitions = {d.code: d for d in capture_definitions(policy)}
    run = CaptureRun(
        s,
        definitions,
        UnitService(s),
        load_holidays(s, country="MX", scope=HolidayScope.BANXICO),
    )
    assert write_event(run, definitions[code], txn, late=True) is not None
    reopen_touched(run)


def someone_waits(admin_engine: Engine) -> bool:
    """Wait until a session waits for a lock, or give up.

    Returns:
        Whether one did.

    """
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while time.monotonic() < deadline:
        with admin_engine.connect() as connection:
            waiting = connection.scalar(
                text("SELECT count(*) FROM pg_locks WHERE NOT granted")
            )
        if waiting:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.usefixtures("forget_hooks", "new_year")
def test_a_close_waits_for_an_event_being_written_and_counts_it(
    tenant: CreatedTenant, admin_engine: Engine
):
    stage = golden(tenant, admin_engine, ROR)
    ror_1 = by_external_id(stage, "ror-1")
    unprocess(stage, ror_1)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with tenant_session(system_ctx(stage.tenant_id)) as s:
            process_capture(s, ror_1, ROR)
            closing = pool.submit(close_one, stage, ROR, Q4)
            # The close blocks on the period until the writer commits.
            assert someone_waits(admin_engine)
        closed = closing.result(timeout=30)
    finally:
        pool.shutdown()

    assert closed.status is CapturePeriodStatus.CLOSED
    assert closed.event_count == closed.reconciled_count == 2
    assert closed.discrepancies is None
    assert period_of(stage, ROR, Q4).change_count == 0


@pytest.mark.usefixtures("forget_hooks", "new_year")
def test_an_event_written_after_the_close_reopens_the_period(
    tenant: CreatedTenant, admin_engine: Engine
):
    stage = golden(tenant, admin_engine, ROR)
    ror_1 = by_external_id(stage, "ror-1")
    unprocess(stage, ror_1)

    def write() -> None:
        with tenant_session(system_ctx(stage.tenant_id)) as s:
            process_capture(s, ror_1, ROR)

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with tenant_session(system_ctx(stage.tenant_id)) as s:
            period = s.scalars(
                select(CapturePeriod).where(CapturePeriod.capture_code == ROR)
            ).one()
            closed = periods_module.close_period(s, period, units=UnitService(s))
            assert closed.event_count == 1
            writing = pool.submit(write)
            # The writer blocks on the period until the close commits.
            assert someone_waits(admin_engine)
        writing.result(timeout=30)
    finally:
        pool.shutdown()

    after = period_of(stage, ROR, Q4)
    assert after.status is CapturePeriodStatus.REOPENED
    assert after.change_count == 1
    (changed,) = outbox(stage, CAPTURE_PERIOD_CHANGED)
    assert changed.data["capture_period_id"] == str(after.id)
    live = [e for e in events(stage, ROR) if e.transaction_id == ror_1]
    assert [e.status for e in live] == [ReportableEventStatus.CAPTURED]

    close_all(stage)
    again = period_of(stage, ROR, Q4)
    assert again.status is CapturePeriodStatus.CLOSED
    assert again.event_count == again.reconciled_count == 2
    assert again.discrepancies is None


@pytest.mark.usefixtures("forget_hooks", "new_year")
def test_a_voided_extra_is_published_and_gives_back_what_merged_into_it(
    tenant: CreatedTenant, admin_engine: Engine
):
    # avb-1 fired both captures; only its AVI_XVI_A event was written, naming
    # AVI_XVI_B. Taken out of the processed rows, its A event is extra.
    stage = golden(tenant, admin_engine, AVI_B)
    avb_1 = by_external_id(stage, "avb-1")
    (a_event,) = [e for e in events(stage, AVI_A) if e.transaction_id == avb_1]
    assert a_event.fields["merged_captures"] == [AVI_B]
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        _ = s.execute(
            update(Transaction)
            .where(Transaction.id == avb_1)
            .values(status=TransactionStatus.RECEIVED)
        )
    published = len(outbox(stage, REPORTABLE_EVENT_CAPTURED))

    close_all(stage, {"capture_code": AVI_A, "period_key": "2026-12"})

    closed = period_of(stage, AVI_A, "2026-12")
    assert closed.discrepancies == {"missing": [], "extra": [str(avb_1)]}
    (voided,) = [e for e in events(stage, AVI_A) if e.transaction_id == avb_1]
    assert voided.status is ReportableEventStatus.VOIDED
    assert voided.void_reason == "RECONCILIATION"
    (superseded,) = outbox(stage, REPORTABLE_EVENT_SUPERSEDED)
    assert superseded.data == {
        "reportable_event_id": str(voided.id),
        "superseded_by_id": None,
        "period_key": "2026-12",
    }
    (fee,) = [e for e in events(stage, AVI_B) if e.transaction_id == avb_1]
    assert fee.status is ReportableEventStatus.CAPTURED
    assert fee.late
    assert len(outbox(stage, REPORTABLE_EVENT_CAPTURED)) == published + 1


@pytest.mark.usefixtures("forget_hooks", "new_year")
def test_the_check_streams_its_rows(tenant: CreatedTenant, admin_engine: Engine):
    stage = golden(tenant, admin_engine, ROR)
    streamed: list[object] = []

    def watch(state: ORMExecuteState) -> None:
        # Whole transaction rows, not the column reads beside them.
        statement = state.statement
        if isinstance(statement, Select) and any(
            description["type"] is Transaction
            for description in statement.column_descriptions
        ):
            streamed.append(state.execution_options.get("yield_per"))

    event.listen(Session, "do_orm_execute", watch)
    try:
        with tenant_session(system_ctx(stage.tenant_id)) as s:
            period = s.scalars(
                select(CapturePeriod).where(CapturePeriod.capture_code == ROR)
            ).one()
            definition = definition_for(s, period)
            assert definition is not None
            expected = check_captures(
                s,
                definition,
                period_key=Q4,
                units=UnitService(s),
                holidays=load_holidays(s, country="MX", scope=HolidayScope.BANXICO),
            )
    finally:
        event.remove(Session, "do_orm_execute", watch)

    assert len(expected) == 2
    assert streamed
    assert all(isinstance(size, int) and size > 0 for size in streamed)


def at(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 10, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)


@pytest.mark.usefixtures("forget_hooks")
def test_a_long_lived_relationship_is_not_walked_back_through_its_history(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    # One operation on the 15th of every month from June: every operation's own
    # window reaches the next six, so a walk from the rows alone goes back to June.
    stage = build_stage(tenant, admin_engine, more_units=[UNIT_VALUES_EXTRA])
    _ = add_relationship(stage, "crypto-1", product="CRYPTO")
    months = [(2026, m) for m in range(6, 13)] + [(2027, 1)]
    _ = feed(
        stage,
        [
            crypto_item(f"m-{year}-{month:02d}", at(year, month, 15), "4000.00")
            for year, month in months
        ],
    )
    february = datetime(2027, 2, 1, 4, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)
    monkeypatch.setattr(clock, "now", lambda: february)
    reads: list[tuple[datetime, datetime]] = []
    real = groups_module.members_between

    def counting(*args: object, **kwargs: object) -> object:
        start, end = kwargs["start"], kwargs["end"]
        assert isinstance(start, datetime)
        assert isinstance(end, datetime)
        reads.append((start, end))
        return cast("Callable[..., object]", real)(*args, **kwargs)

    monkeypatch.setattr(periods_module, "members_between", counting)

    with tenant_session(system_ctx(stage.tenant_id)) as s:
        definition = periods_module.definitions_on(s, date(2027, 1, 31))[AVI_A]
        expected = check_captures(
            s,
            definition,
            period_key="2027-01",
            units=UnitService(s),
            holidays=load_holidays(s, country="MX", scope=HolidayScope.BANXICO),
        )

    # June to November (six of 34.10 UMA) stay below 210 in the first window,
    # which ends on 15 December; December opens the next, holding January.
    assert expected == set()
    assert len(reads) <= 2
    assert min(start for start, _ in reads) >= at(2026, 12, 1)


@pytest.mark.usefixtures("forget_hooks", "new_year")
def test_one_failing_period_does_not_block_the_others(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    stage = golden(tenant, admin_engine, ROR)
    real = periods_module.close_period

    def failing(s: Session, period: CapturePeriod, *, units: UnitService) -> object:
        if period.capture_code == "ITF_FX_CASH":
            msg = "a unit value is missing"
            raise RuntimeError(msg)
        return real(s, period, units=units)

    monkeypatch.setattr(periods_module, "close_period", failing)

    with pytest.raises(RuntimeError, match="1 of 6 capture periods failed"):
        close_all(stage)

    status = {p.capture_code: p.status for p in periods(stage)}
    assert status.pop("ITF_FX_CASH") is CapturePeriodStatus.OPEN
    assert set(status.values()) == {CapturePeriodStatus.CLOSED}


@pytest.mark.usefixtures("forget_hooks", "new_year")
def test_a_close_by_hand_is_audited(tenant: CreatedTenant, admin_engine: Engine):
    stage = golden(tenant, admin_engine, ROR)
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        period = s.scalars(
            select(CapturePeriod).where(CapturePeriod.capture_code == ROR)
        ).one()
        closed = close_period_by_hand(s, period, units=UnitService(s))
        closed_id = closed.id

    (row,) = [
        r for r in audit_events(stage.tenant_id) if r.action == "captures.period_closed"
    ]
    assert (row.entity_type, row.entity_id) == ("capture_period", str(closed_id))
    assert row.detail == {
        "capture_code": ROR,
        "period_key": Q4,
        "discrepancy_count": 0,
        "change_count": 0,
    }


@pytest.mark.usefixtures("forget_hooks")
def test_ensure_periods_starts_after_the_last_close(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    stage = golden(tenant, admin_engine, AVI_A)
    monkeypatch.setattr(clock, "now", lambda: NEW_YEAR)
    close_all(stage)
    days: list[date] = []
    real = periods_module.definitions_on

    def counting(s: Session, on: date) -> object:
        days.append(on)
        return real(s, on)

    monkeypatch.setattr(periods_module, "definitions_on", counting)
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        created = ensure_periods(s, ended_before=date(2027, 2, 1))

    # July to December were closed: only January is new.
    assert days == [date(2027, 1, 31)]
    assert created == 3
