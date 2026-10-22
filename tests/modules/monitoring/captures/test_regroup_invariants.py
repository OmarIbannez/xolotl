"""Regrouping under attack (B10 re-review): late rows, boundaries, retries, gaps.

Every scenario ends by checking three invariants of ``AVI_XVI_A``: one event per
transaction, live group windows never overlap, and every live grouped event
points at a live crossed group. The base: five monthly MXN 4,000 operations from
15 July 2026 (34.10 UMA each) and a December one.
"""

import itertools
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import Engine, select

from tests.helpers import system_ctx
from tests.modules.monitoring.captures.support import (
    UNIT_VALUES_EXTRA,
    CaptureStage,
    add_relationship,
    build_stage,
    crypto_item,
    events,
    feed,
    groups,
)
from tests.modules.monitoring.captures.test_definitions import pack_policy
from tests.modules.monitoring.captures.test_regroup import backfill
from xolotl.modules.monitoring.aggregates.memory import MemoryAggregateStore
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.captures.definitions import capture_definitions
from xolotl.modules.monitoring.captures.enums import (
    CaptureGroupStatus,
    ReportableEventStatus,
)
from xolotl.modules.monitoring.captures.events import capture_transaction
from xolotl.modules.monitoring.captures.models import CaptureGroup, ReportableEvent
from xolotl.modules.monitoring.enrich.service import enriched_from_row
from xolotl.modules.monitoring.process.hooks import reset_processing_hooks
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.calendar import load_holidays
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import HolidayScope
from xolotl.platform.units import UnitService

CODE = "AVI_XVI_A"
CAPTURED = ReportableEventStatus.CAPTURED
CROSSED = CaptureGroupStatus.CROSSED


def at(year: int, month: int, day: int, hour: int = 10, minute: int = 0) -> datetime:
    local = datetime(year, month, day, hour, minute, tzinfo=MEXICO_CITY_ZONE)
    return local.astimezone(UTC)


@pytest.fixture
def stage(tenant: CreatedTenant, admin_engine: Engine) -> Iterator[CaptureStage]:
    """Build the capture stage with June 2026 and early 2027 unit values too.

    Yields:
        The stage; the processing hooks are forgotten afterwards.

    """
    yield build_stage(tenant, admin_engine, more_units=[UNIT_VALUES_EXTRA])
    reset_processing_hooks()


def base(stage: CaptureStage, *, last: str = "5000.00") -> list[UUID]:
    _ = add_relationship(stage, "crypto-1", product="CRYPTO")
    items = [
        crypto_item(f"m{month}", at(2026, month, 15), "4000.00")
        for month in range(7, 12)
    ]
    items.append(crypto_item("dec", at(2026, 12, 11), last))
    return feed(stage, items)


def invariants(
    stage: CaptureStage,
) -> tuple[list[ReportableEvent], list[CaptureGroup]]:
    """Check the three invariants and return the events and live groups.

    Returns:
        The capture's events and its live groups, by window start.

    """
    found = events(stage, CODE)
    assert all(n == 1 for n in Counter(e.transaction_id for e in found).values())
    live = sorted(
        (
            g
            for g in groups(stage)
            if g.capture_code == CODE and g.status is not CaptureGroupStatus.SUPERSEDED
        ),
        key=lambda g: g.window_start,
    )
    for earlier, later in itertools.pairwise(live):
        assert earlier.window_end <= later.window_start
    by_id = {g.id: g for g in live}
    for event in found:
        if event.status is CAPTURED and event.capture_group_id is not None:
            assert by_id[event.capture_group_id].status is CROSSED
    return found, live


def captured(found: list[ReportableEvent]) -> set[UUID]:
    return {e.transaction_id for e in found if e.status is CAPTURED}


def test_two_late_operations_out_of_order_cross_at_the_earliest(stage: CaptureStage):
    ids = base(stage)
    (second,) = feed(
        stage,
        [crypto_item("l2", at(2026, 12, 10), "5000.00")],
        received=at(2026, 12, 12),
    )
    (first,) = feed(
        stage,
        [crypto_item("l1", at(2026, 12, 9), "5000.00")],
        received=at(2026, 12, 12, 11),
    )

    found, live = invariants(stage)
    (group,) = live
    assert group.status is CROSSED
    assert group.threshold_crossed_at == at(2026, 12, 9)
    assert captured(found) == {*ids, first, second}


def test_a_late_operation_makes_an_open_window_cross(stage: CaptureStage):
    ids = base(stage, last="1000.00")
    assert events(stage, CODE) == []

    (late,) = feed(
        stage,
        [crypto_item("l", at(2026, 12, 10), "4000.00")],
        received=at(2026, 12, 12),
    )

    found, live = invariants(stage)
    (group,) = live
    assert group.status is CROSSED
    assert group.threshold_crossed_at == at(2026, 12, 11)
    assert captured(found) == {*ids, late}


def test_the_window_boundary_belongs_to_the_next_group(stage: CaptureStage):
    ids = base(stage)  # one group, [15 July, 15 January), crossed on 11 December
    (january,) = feed(stage, [crypto_item("j", at(2027, 1, 20), "5000.00")])
    (boundary,) = feed(
        stage,
        [crypto_item("l", at(2027, 1, 15, 0, 0), "20000.00")],
        received=at(2027, 1, 20, 12),
    )

    found, live = invariants(stage)
    (first,) = [g for g in live if g.window_start == at(2026, 7, 15, 0)]
    (second,) = [g for g in live if g.window_start == at(2027, 1, 15, 0)]
    assert first.status is CROSSED
    assert second.status is CROSSED
    by_txn = {e.transaction_id: e for e in found}
    assert by_txn[boundary].capture_group_id == second.id
    assert by_txn[january].capture_group_id == second.id
    assert all(by_txn[txn].capture_group_id == first.id for txn in ids)

    # A minute before the boundary joins the first group instead.
    (inside,) = feed(
        stage,
        [crypto_item("k", at(2027, 1, 14, 23, 59), "100.00")],
        received=at(2027, 1, 20, 13),
    )

    found, _ = invariants(stage)
    event = next(e for e in found if e.transaction_id == inside)
    assert event.capture_group_id == first.id
    assert event.status is CAPTURED


def test_capturing_the_same_late_row_again_changes_nothing(stage: CaptureStage):
    _ = base(stage)
    (late,) = feed(
        stage,
        [crypto_item("l", at(2026, 12, 10), "5000.00")],
        received=at(2026, 12, 12),
    )
    before = sorted((e.id, e.status, e.capture_group_id) for e in events(stage, CODE))
    group_count = len(groups(stage))
    definitions = capture_definitions(pack_policy("MX/LFPIORPI-AVI"))

    for _ in range(2):
        with tenant_session(system_ctx(stage.tenant_id)) as s:
            row = s.scalars(select(Transaction).where(Transaction.id == late)).one()
            outcome = capture_transaction(
                s,
                definitions,
                enriched_from_row(row),
                store=MemoryAggregateStore(),
                units=UnitService(s),
                holidays=load_holidays(s, country="MX", scope=HolidayScope.BANXICO),
                late=True,
            )
            assert outcome.events == ()
            assert outcome.superseded == ()

    after = sorted((e.id, e.status, e.capture_group_id) for e in events(stage, CODE))
    assert after == before
    assert len(groups(stage)) == group_count
    _ = invariants(stage)


def test_a_backfill_past_the_stores_reach_regroups_from_the_rows(
    stage: CaptureStage,
):
    _ = add_relationship(stage, "crypto-1", product="CRYPTO")
    items = [
        crypto_item(f"m{month}", at(2026, month, 25), "4000.00")
        for month in range(7, 13)
    ]
    items.append(crypto_item("j22", at(2027, 1, 22), "4000.00"))
    ids = feed(stage, items)
    found, _ = invariants(stage)
    assert len(captured(found)) == len(ids)

    # The store keeps six months back from now: the 20 July row is past it.
    _ = backfill(
        stage, crypto_item("late", at(2026, 7, 20), "1000.00"), now=at(2027, 1, 23, 12)
    )

    found, _ = invariants(stage)
    by_txn = {e.transaction_id: e for e in found}
    # The window now ends on 20 January: 22 January is no longer reportable.
    assert by_txn[ids[-1]].status is ReportableEventStatus.SUPERSEDED
    assert all(by_txn[txn].status is CAPTURED for txn in ids[:-1])

    (after,) = feed(stage, [crypto_item("t", at(2027, 1, 24), "1000.00")])

    found, live = invariants(stage)
    (january,) = [g for g in live if g.window_start == at(2027, 1, 22, 0)]
    assert january.status is CaptureGroupStatus.OPEN
    assert after not in {e.transaction_id for e in found}
