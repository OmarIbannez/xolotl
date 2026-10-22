"""Capture periods and the period close (SP-04 spec §10.4; plan 4b B11).

The close re-runs the period's captures over its ``PROCESSED`` transactions in
check mode, writes what is missing (``late``), voids what should not be there
(``RECONCILIATION``) and closes the period. On clean data it finds nothing: every
capture golden closes with zero discrepancies.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from sqlalchemy import Engine, delete, func, select, update

from tests.helpers import new_tenant, system_ctx
from tests.modules.monitoring.captures.support import (
    UNIT_VALUES_EXTRA,
    CaptureStage,
    add_relationship,
    build_stage,
    crypto_item,
    events,
    external_ids,
    feed,
    outbox,
    periods,
)
from tests.modules.monitoring.captures.test_capture_goldens import (
    CODES,
    GOLDEN,
    run_golden,
)
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.captures.enums import (
    CapturePeriodStatus,
    ReportableEventStatus,
)
from xolotl.modules.monitoring.captures.models import (
    CaptureGroup,
    CapturePeriod,
    ReportableEvent,
)
from xolotl.modules.monitoring.captures.periods import (
    PERIOD_CLOSE_KIND,
    check_captures,
    close_period,
    definition_for,
    ensure_periods,
    handle_period_close,
)
from xolotl.modules.monitoring.captures.record import reopen_period
from xolotl.modules.monitoring.errors import PeriodNotEndedError
from xolotl.modules.monitoring.events import (
    CAPTURE_PERIOD_CHANGED,
    CAPTURE_PERIOD_CLOSED,
)
from xolotl.modules.monitoring.process.hooks import reset_processing_hooks
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.calendar import load_holidays
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import HolidayScope
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.models import JobSchedule
from xolotl.platform.jobs.queue import Job
from xolotl.platform.units import UnitService

NEW_YEAR = datetime(2027, 1, 1, 4, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)
ROR = "ITF_ROR_CASH"
AVI_A = "AVI_XVI_A"
Q4 = "2026-Q4"


@pytest.fixture
def forget_hooks() -> Iterator[None]:
    yield
    reset_processing_hooks()


@pytest.fixture
def new_year(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Run at 04:00 on 1 January 2027 in Mexico City, the schedule's slot.

    Returns:
        The instant.

    """
    monkeypatch.setattr(clock, "now", lambda: NEW_YEAR)
    return NEW_YEAR


def golden(tenant: CreatedTenant, admin_engine: Engine, code: str) -> CaptureStage:
    directory = GOLDEN / code
    stage = build_stage(tenant, admin_engine, directory / "units.yaml")
    _ = run_golden(stage, directory)
    return stage


def close_job(stage: CaptureStage, payload: dict[str, str] | None = None) -> Job:
    return Job(
        id=uuid7(),
        tenant_id=stage.tenant_id,
        kind=PERIOD_CLOSE_KIND,
        key=None,
        payload={**(payload or {})},
        attempts=1,
        max_attempts=5,
        lock_token=uuid7(),
    )


def close_all(stage: CaptureStage, payload: dict[str, str] | None = None) -> None:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        handle_period_close(s, close_job(stage, payload))


def period_of(stage: CaptureStage, code: str, key: str) -> CapturePeriod:
    (found,) = [
        p for p in periods(stage) if (p.capture_code, p.period_key) == (code, key)
    ]
    return found


def close_one(stage: CaptureStage, code: str, key: str) -> CapturePeriod:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        period = s.scalars(
            select(CapturePeriod).where(
                CapturePeriod.capture_code == code, CapturePeriod.period_key == key
            )
        ).one()
        return close_period(s, period, units=UnitService(s))


def by_external_id(stage: CaptureStage, external_id: str) -> UUID:
    (found,) = [k for k, v in external_ids(stage).items() if v == external_id]
    return found


def occurred_at(stage: CaptureStage, transaction_id: UUID) -> datetime:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        return s.scalars(
            select(Transaction.occurred_at).where(Transaction.id == transaction_id)
        ).one()


def counts(stage: CaptureStage) -> tuple[int, ...]:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        return tuple(
            s.scalar(select(func.count()).select_from(model)) or 0
            for model in (ReportableEvent, CaptureGroup, CapturePeriod, OutboxRow)
        )


def last_period_key(stage: CaptureStage, code: str) -> str:
    live = [
        e for e in events(stage, code) if e.status is ReportableEventStatus.CAPTURED
    ]
    return live[-1].period_key


@pytest.mark.usefixtures("forget_hooks", "new_year")
def test_periods_are_opened_for_every_capture_of_the_ended_month(
    tenant: CreatedTenant, admin_engine: Engine
):
    stage = golden(tenant, admin_engine, ROR)
    assert [(p.capture_code, p.period_key) for p in periods(stage)] == [(ROR, Q4)]

    with tenant_session(system_ctx(stage.tenant_id)) as s:
        created = ensure_periods(s, ended_before=date(2027, 1, 1))
        again = ensure_periods(s, ended_before=date(2027, 1, 1))
        # December has not ended on its last day.
        early = ensure_periods(s, ended_before=date(2026, 12, 31))

    assert (created, again, early) == (5, 0, 0)
    assert {(p.capture_code, p.period_key) for p in periods(stage)} == {
        ("AVI_XVI_A", "2026-12"),
        ("AVI_XVI_B", "2026-12"),
        ("ITF_FX_CASH", Q4),
        ("ITF_INTL_TRANSFER", "2026-12"),
        (ROR, Q4),
        ("ITF_VA_OPS", Q4),
    }
    december = period_of(stage, AVI_A, "2026-12")
    assert (december.period_start, december.period_end) == (
        date(2026, 12, 1),
        date(2026, 12, 31),
    )
    assert december.report_code == "MX_AVISO_AVI"
    assert december.status is CapturePeriodStatus.OPEN


@pytest.mark.usefixtures("forget_hooks", "new_year")
@pytest.mark.parametrize("code", CODES)
def test_a_clean_period_closes_with_zero_discrepancies(
    code: str, tenant: CreatedTenant, admin_engine: Engine
):
    stage = golden(tenant, admin_engine, code)
    before = {(e.id, e.status, e.late) for e in events(stage)}

    close_all(stage)

    assert {(e.id, e.status, e.late) for e in events(stage)} == before
    rows = periods(stage)
    for period in rows:
        key = (period.capture_code, period.period_key)
        assert period.status is CapturePeriodStatus.CLOSED, key
        assert period.discrepancies is None, key
        assert period.reconciled_count == period.event_count, key
        assert period.closed_at == NEW_YEAR
        assert period.reconciled_at == NEW_YEAR
    mine = period_of(stage, code, last_period_key(stage, code))
    assert mine.event_count > 0
    closed = outbox(stage, CAPTURE_PERIOD_CLOSED)
    assert len(closed) == len(rows)
    (published,) = [e for e in closed if e.data["capture_period_id"] == str(mine.id)]
    assert published.data == {
        "capture_period_id": str(mine.id),
        "capture_code": code,
        "report_code": mine.report_code,
        "period_key": mine.period_key,
        "status": "CLOSED",
        "discrepancy_count": 0,
    }
    assert outbox(stage, CAPTURE_PERIOD_CHANGED) == []


@pytest.mark.usefixtures("forget_hooks", "new_year")
def test_the_close_finds_an_injected_gap_and_writes_the_missing_event(
    tenant: CreatedTenant, admin_engine: Engine
):
    stage = golden(tenant, admin_engine, ROR)
    ror_1 = by_external_id(stage, "ror-1")
    ror_2 = by_external_id(stage, "ror-2")
    (gone,) = [e for e in events(stage, ROR) if e.transaction_id == ror_1]
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        _ = s.execute(delete(ReportableEvent).where(ReportableEvent.id == gone.id))
        # An event for ror-2, USD 4,999: below the threshold, so extra.
        s.add(
            ReportableEvent(
                tenant_id=stage.tenant_id,
                id=uuid7(),
                capture_code=ROR,
                report_code=gone.report_code,
                capture_kind=gone.capture_kind,
                transaction_id=ror_2,
                occurred_at=occurred_at(stage, ror_2),
                party_id=gone.party_id,
                relationship_id=gone.relationship_id,
                product=gone.product,
                period_key=Q4,
                fields=gone.fields,
                unit_values=gone.unit_values,
                policy_snapshot_id=gone.policy_snapshot_id,
            )
        )

    closed = close_one(stage, ROR, Q4)

    assert closed.status is CapturePeriodStatus.CLOSED
    assert closed.discrepancies == {"missing": [str(ror_1)], "extra": [str(ror_2)]}
    assert closed.reconciled_count == closed.event_count == 2
    by_txn = {e.transaction_id: e for e in events(stage, ROR)}
    assert by_txn[ror_1].status is ReportableEventStatus.CAPTURED
    assert by_txn[ror_1].late
    assert by_txn[ror_1].fields == gone.fields
    assert by_txn[ror_2].status is ReportableEventStatus.VOIDED
    assert by_txn[ror_2].void_reason == "RECONCILIATION"
    (published,) = outbox(stage, CAPTURE_PERIOD_CLOSED)
    assert published.data["discrepancy_count"] == 2
    assert outbox(stage, CAPTURE_PERIOD_CHANGED) == []


@pytest.mark.usefixtures("forget_hooks", "new_year")
def test_the_close_brings_back_a_lost_member_of_a_crossed_group(
    tenant: CreatedTenant, admin_engine: Engine
):
    stage = golden(tenant, admin_engine, AVI_A)
    july = by_external_id(stage, "avi-07")
    (lost,) = [e for e in events(stage, AVI_A) if e.transaction_id == july]
    assert lost.capture_group_id is not None
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        _ = s.execute(
            update(ReportableEvent)
            .where(ReportableEvent.id == lost.id)
            .values(status=ReportableEventStatus.VOIDED, void_reason="BY HAND")
        )

    close_all(stage, {"capture_code": AVI_A, "period_key": "2026-07"})

    july_period = period_of(stage, AVI_A, "2026-07")
    assert july_period.status is CapturePeriodStatus.CLOSED
    assert july_period.discrepancies == {"missing": [str(july)], "extra": []}
    (back,) = [e for e in events(stage, AVI_A) if e.transaction_id == july]
    assert back.id == lost.id
    assert back.status is ReportableEventStatus.CAPTURED
    assert back.capture_group_id == lost.capture_group_id
    assert back.void_reason is None
    assert back.late
    # Only the named period was closed.
    assert [p.period_key for p in periods(stage) if p.status.value == "CLOSED"] == [
        "2026-07"
    ]


@pytest.mark.usefixtures("forget_hooks", "new_year")
def test_voiding_an_included_event_tells_reporting_the_period_changed(
    tenant: CreatedTenant, admin_engine: Engine
):
    stage = golden(tenant, admin_engine, ROR)
    ror_2 = by_external_id(stage, "ror-2")
    template = events(stage, ROR)[0]
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        # Reported, yet the data never supported it: USD 4,999.
        s.add(
            ReportableEvent(
                tenant_id=stage.tenant_id,
                id=uuid7(),
                capture_code=ROR,
                report_code=template.report_code,
                capture_kind=template.capture_kind,
                transaction_id=ror_2,
                occurred_at=occurred_at(stage, ror_2),
                party_id=template.party_id,
                relationship_id=template.relationship_id,
                product=template.product,
                period_key=Q4,
                fields=template.fields,
                unit_values=template.unit_values,
                policy_snapshot_id=template.policy_snapshot_id,
                status=ReportableEventStatus.INCLUDED,
            )
        )

    closed = close_one(stage, ROR, Q4)

    assert closed.status is CapturePeriodStatus.CLOSED
    assert closed.discrepancies == {"missing": [], "extra": [str(ror_2)]}
    assert closed.change_count == 1
    assert closed.reopened_at == NEW_YEAR
    (changed,) = outbox(stage, CAPTURE_PERIOD_CHANGED)
    assert changed.data["status"] == "REOPENED"
    assert changed.data["discrepancy_count"] == 1
    (voided,) = [e for e in events(stage, ROR) if e.transaction_id == ror_2]
    assert voided.status is ReportableEventStatus.VOIDED
    assert voided.void_reason == "RECONCILIATION"


@pytest.mark.usefixtures("forget_hooks")
def test_a_period_that_has_not_ended_cannot_be_closed(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    stage = golden(tenant, admin_engine, ROR)
    last_day = datetime(2026, 12, 31, 23, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)
    monkeypatch.setattr(clock, "now", lambda: last_day)

    with pytest.raises(PeriodNotEndedError) as caught:
        _ = close_one(stage, ROR, Q4)

    assert caught.value.code == "PERIOD_NOT_ENDED"
    assert period_of(stage, ROR, Q4).status is CapturePeriodStatus.OPEN
    # The scheduled run leaves it alone.
    close_all(stage)
    assert period_of(stage, ROR, Q4).status is CapturePeriodStatus.OPEN


@pytest.mark.usefixtures("forget_hooks", "new_year")
def test_check_mode_writes_nothing(tenant: CreatedTenant, admin_engine: Engine):
    stage = golden(tenant, admin_engine, AVI_A)
    before = counts(stage)
    captured = {
        e.transaction_id
        for e in events(stage, AVI_A)
        if e.status is ReportableEventStatus.CAPTURED and e.period_key == "2026-07"
    }

    with tenant_session(system_ctx(stage.tenant_id)) as s:
        period = s.scalars(
            select(CapturePeriod).where(CapturePeriod.capture_code == AVI_A)
        ).first()
        assert period is not None
        definition = definition_for(s, period)
        assert definition is not None
        expected = check_captures(
            s,
            definition,
            period_key="2026-07",
            units=UnitService(s),
            holidays=load_holidays(s, country="MX", scope=HolidayScope.BANXICO),
        )

    assert expected == captured
    assert len(expected) == 1
    assert counts(stage) == before


@pytest.mark.usefixtures("forget_hooks", "new_year")
def test_a_reopened_period_closes_again_with_its_change_count(
    tenant: CreatedTenant, admin_engine: Engine
):
    stage = golden(tenant, admin_engine, ROR)
    _ = close_one(stage, ROR, Q4)
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        reopened = reopen_period(s, ROR, Q4)
        assert reopened is not None
        assert reopened.status is CapturePeriodStatus.REOPENED

    close_all(stage)

    again = period_of(stage, ROR, Q4)
    assert again.status is CapturePeriodStatus.CLOSED
    assert again.change_count == 1
    assert again.reopened_at == NEW_YEAR
    assert again.discrepancies is None
    assert len(outbox(stage, CAPTURE_PERIOD_CLOSED)) == len(periods(stage)) + 1


def test_the_schedule_is_declared_per_tenant(engine: Engine):
    created = new_tenant()
    with tenant_session(system_ctx(created.tenant_id)) as s:
        row = s.scalars(
            select(JobSchedule).where(JobSchedule.name == PERIOD_CLOSE_KIND)
        ).one()
    assert (row.name, row.cron, row.tz, row.kind, row.payload, row.enabled) == (
        "monitoring.period_close",
        "0 4 1 * *",
        "America/Mexico_City",
        "monitoring.period_close",
        {},
        True,
    )


def at(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 10, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)


@pytest.mark.usefixtures("forget_hooks")
def test_the_check_chains_windows_from_before_the_lookback(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    # 15 June opens a window to 15 December holding 10 July (8.52 + 187.54 UMA:
    # no crossing); 20 December opens the next, holding 5 January (68.20 UMA).
    # Grouping from six months before January would put 10 July, 20 December and
    # 5 January in one window that crosses 210 UMA.
    stage = build_stage(tenant, admin_engine, more_units=[UNIT_VALUES_EXTRA])
    _ = add_relationship(stage, "crypto-1", product="CRYPTO")
    _ = feed(
        stage,
        [
            crypto_item("w-0", at(2026, 6, 15), "1000.00"),
            crypto_item("w-1", at(2026, 7, 10), "22000.00"),
            crypto_item("w-2", at(2026, 12, 20), "4000.00"),
            crypto_item("w-3", at(2027, 1, 5), "4000.00"),
        ],
    )
    assert events(stage, AVI_A) == []
    february = datetime(2027, 2, 1, 4, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)
    monkeypatch.setattr(clock, "now", lambda: february)

    close_all(stage)

    january = period_of(stage, AVI_A, "2027-01")
    assert january.status is CapturePeriodStatus.CLOSED
    assert january.discrepancies is None
    assert january.reconciled_count == january.event_count == 0
    assert all(p.discrepancies is None for p in periods(stage))
    assert events(stage, AVI_A) == []
