"""The Postgres store, distinct members, completeness and declarations (§8.1-§8.4)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import new_tenant, system_ctx
from tests.modules.monitoring.aggregates import scenarios
from tests.modules.monitoring.support import declare
from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateFunction,
    WindowKind,
)
from xolotl.modules.monitoring.aggregates.memory import MemoryAggregateStore
from xolotl.modules.monitoring.aggregates.models import AggDeclaration, AggDistinct
from xolotl.modules.monitoring.aggregates.postgres import (
    AGG_BACKFILL_KIND,
    PostgresAggregateStore,
    ensure_declarations,
    load_declarations,
)
from xolotl.modules.monitoring.enums import BucketKind
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import PostgresJobQueue


def _complete(s: Session) -> PostgresAggregateStore:
    return PostgresAggregateStore(s, complete_ids=_ALL_IDS)


_ALL_IDS = {scenarios.declaration(f).agg_id() for f in AggregateFunction} | {
    scenarios.ACCUM.agg_id()
}


@pytest.mark.parametrize("function", list(AggregateFunction))
def test_each_function_over_a_small_stream(s: Session, function: AggregateFunction):
    assert (
        scenarios.each_function(_complete(s), function) == scenarios.EXPECTED[function]
    )


def test_a_transaction_outside_the_filter_does_not_contribute(s: Session):
    assert scenarios.outside_the_filter(_complete(s)) == (3, 3)


def test_accumulators_open_and_close_per_key(s: Session):
    assert scenarios.accumulators(_complete(s)) == scenarios.ACCUM_EXPECTED


def test_distinct_members_are_hmacs_not_values(s: Session):
    _ = scenarios.each_function(_complete(s), AggregateFunction.DISTINCT_COUNT)
    stored = s.scalars(select(AggDistinct.member_hash)).all()
    # One member row per bucket: C1 in two hours, C2 in one.
    assert (len(stored), len(set(stored))) == (3, 2)
    for member in (scenarios.C1, scenarios.C2):
        assert bytes.fromhex(member) not in stored
        assert member.encode() not in stored
        assert all(member.encode() not in hashed for hashed in stored)
    assert all(len(hashed) == 32 for hashed in stored)


def test_reads_are_incomplete_until_backfilled_through_is_set(
    s: Session, queue: PostgresJobQueue
):
    declared = scenarios.declaration(AggregateFunction.SUM)
    store = PostgresAggregateStore(s)
    assert store.read(scenarios.KEY, declared, at=scenarios.READ_AT).complete is False
    _ = ensure_declarations(s, [declared], queue=queue)
    for row in scenarios.SMALL:
        store.add(row, [declared])
    value = store.read(scenarios.KEY, declared, at=scenarios.READ_AT)
    assert (value.value, value.complete) == (
        scenarios.EXPECTED[AggregateFunction.SUM],
        False,
    )
    row = s.scalars(select(AggDeclaration)).one()
    row.backfilled_through = datetime.now(UTC)
    s.flush()
    assert store.read(scenarios.KEY, declared, at=scenarios.READ_AT).complete is True
    assert (
        PostgresAggregateStore(s, complete_ids=())
        .read(scenarios.KEY, declared, at=scenarios.READ_AT)
        .complete
        is False
    )


def test_ensure_declarations_inserts_once_and_enqueues_a_backfill_per_new_id(
    s: Session, queue: PostgresJobQueue
):
    total = scenarios.declaration(AggregateFunction.SUM)
    same = scenarios.declaration(AggregateFunction.SUM, name="another_name")
    count = scenarios.declaration(AggregateFunction.COUNT)
    assert ensure_declarations(s, [total, same], queue=queue) == [total.agg_id()]
    assert ensure_declarations(s, [total, count], queue=queue) == [count.agg_id()]
    jobs = s.execute(
        select(JobRow.key, JobRow.payload).where(JobRow.kind == AGG_BACKFILL_KIND)
    ).all()
    assert sorted(jobs) == sorted(
        (f"agg_backfill:{d.agg_id()}", {"agg_id": d.agg_id()}) for d in (total, count)
    )
    rows = s.scalars(select(AggDeclaration).order_by(AggDeclaration.agg_id)).all()
    assert all(row.backfilled_through is None for row in rows)
    assert {(row.bucket_kind, row.window_kind) for row in rows} == {
        (BucketKind.HOUR, WindowKind.DURATION)
    }
    loaded = load_declarations(s)
    assert {d.agg_id() for d in loaded} == {total.agg_id(), count.agg_id()}
    assert all(d.name == d.agg_id() for d in loaded)


def test_ensure_declarations_refuses_an_invalid_declaration(
    s: Session, queue: PostgresJobQueue
):
    bad = scenarios.declaration(AggregateFunction.SUM, window="P1Y")
    with pytest.raises(ValueError, match="DURATION"):
        _ = ensure_declarations(s, [bad], queue=queue)


def test_buckets_returns_the_window_range_newest_first(
    s: Session, queue: PostgresJobQueue
):
    declared = scenarios.declaration(AggregateFunction.DISTINCT_COUNT)
    _ = declare(s, declared, queue=queue)
    store = PostgresAggregateStore(s)
    for row in scenarios.SMALL:
        store.add(row, [declared])
    first, last = scenarios.T1, scenarios.T3
    got = store.buckets(scenarios.KEY, declared, start=first, end=last)
    assert [b.bucket_start for b in got] == [
        datetime(2026, 12, 2, 9, tzinfo=UTC),
        datetime(2026, 12, 1, 11, tzinfo=UTC),
        datetime(2026, 12, 1, 10, tzinfo=UTC),
    ]
    assert [len(b.members) for b in got] == [1, 1, 1]
    later = store.buckets(
        scenarios.KEY, declared, start=first + timedelta(hours=1), end=last
    )
    assert len(later) == 2
    # The decide path: seed a memory store with the stored buckets and read the same.
    memory = MemoryAggregateStore()
    memory.seed(scenarios.KEY, declared, got)
    assert memory.read(scenarios.KEY, declared, at=scenarios.READ_AT) == store.read(
        scenarios.KEY, declared, at=scenarios.READ_AT
    )


def test_another_tenant_reads_nothing(tenant: CreatedTenant, queue: PostgresJobQueue):
    declared = scenarios.declaration(AggregateFunction.DISTINCT_COUNT)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = declare(s, declared, queue=queue)
        store = PostgresAggregateStore(s)
        for row in scenarios.SMALL:
            store.add(row, [declared])
    other = new_tenant(slug="other", email="admin@other.mx")
    with tenant_session(system_ctx(other.tenant_id)) as s:
        store = PostgresAggregateStore(s)
        value = store.read(scenarios.KEY, declared, at=scenarios.READ_AT)
        assert (value.count, value.distinct, value.complete) == (0, 0, False)
        assert (
            store.buckets(scenarios.KEY, declared, start=scenarios.T1, end=scenarios.T3)
            == []
        )
        assert load_declarations(s) == []
