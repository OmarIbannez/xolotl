"""The replay engine: the evaluator over a memory store, deduplicated (spec §13.1).

``CASH_STRUCTURING`` fires on three deposits in [3,500, 5,000) USD within seven
days summing 5,000 USD or more; its dedup bucket is the UTC week from Monday and
its cooldown seven days, as processing applies them (B7).
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import ClassVar, override
from uuid import UUID

import pytest
from sqlalchemy import event, func, select

from tests.helpers import system_ctx
from tests.modules.monitoring.rules.conftest import (
    enriched,
    itf_policy,
    lists,
    profile,
    usd_amounts,
)
from tests.modules.monitoring.rules.hook_support import (
    CASH,
    Stage,
    add_relationship,
    deposit,
    feed,
    hits,
    local,
)
from tests.modules.monitoring.simulation.support import library_rule
from xolotl.modules.monitoring.aggregates.declarations import AggregateDeclaration
from xolotl.modules.monitoring.aggregates.memory import MemoryAggregateStore
from xolotl.modules.monitoring.rules.assignments import LoadedRule, RuleSet
from xolotl.modules.monitoring.rules.definitions import Dedup, HitOutcome, Severity
from xolotl.modules.monitoring.simulation import engine
from xolotl.modules.monitoring.simulation.engine import (
    SimulatedHit,
    replay_party,
    replay_rule_set,
)
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction, KeyKind
from xolotl.platform.db import tenant_session

RELATIONSHIP = UUID(int=3)


def _near(number: int, at: datetime, usd: str = "3600") -> EnrichedTransaction:
    return enriched(
        id=UUID(int=100 + number),
        external_id=f"dep-{number}",
        occurred_at=at,
        received_at=at,
        unit_amounts=usd_amounts(usd),
    )


def _replay(rules: Sequence[LoadedRule], stream: Sequence[EnrichedTransaction]):
    policy = itf_policy()
    return replay_party(
        rules,
        stream,
        policy_for=lambda _snapshot_id: policy,
        profile_for=lambda _txn: profile(tier="N3"),
        lists=lists(),
        now=local(31),
    )


FIVE = [
    _near(1, local(7)),
    _near(2, local(8)),
    _near(3, local(9)),
    # Same weekly bucket: it repeats the hit.
    _near(4, local(10)),
    # Next week's bucket, inside the cooldown that runs to the 16th at 10:00.
    _near(5, local(14)),
]


def test_replay_dedups_and_cools_down_like_production():
    [hit] = _replay([library_rule(CASH)], FIVE)

    assert hit == SimulatedHit(
        rule_code=CASH,
        rule_version_id=UUID(int=11),
        party_id=UUID(int=2),
        relationship_id=RELATIONSHIP,
        key_kind=KeyKind.RELATIONSHIP,
        key=str(RELATIONSHIP),
        dedup_key=f"RELATIONSHIP:{RELATIONSHIP}:2026-12-07T00:00:00+00:00",
        window_start=local(9) - timedelta(days=7),
        window_end=local(9),
        severity=Severity.MEDIUM,
        outcome=HitOutcome.ALERT,
        aggregates=hit.aggregates,
        trigger_transaction_id=UUID(int=103),
    )
    assert hit.aggregates["near_count"].value == 3
    # Without the cooldown the fifth deposit opens a hit in its own week.
    uncooled = library_rule(
        CASH, dedup=Dedup(key=KeyKind.RELATIONSHIP, window="P7D", cooldown="P0D")
    )
    assert [h.trigger_transaction_id for h in _replay([uncooled], FIVE)] == [
        UUID(int=103),
        UUID(int=105),
    ]


def test_replay_orders_the_stream_by_occurrence():
    shuffled = [FIVE[2], FIVE[0], FIVE[4], FIVE[1], FIVE[3]]
    assert _replay([library_rule(CASH)], shuffled) == _replay(
        [library_rule(CASH)], FIVE
    )


def test_a_hit_after_the_cooldown_is_a_new_hit():
    later = [*FIVE[:3], *(_near(10 + n, local(21 + n)) for n in range(3))]
    found = _replay([library_rule(CASH)], later)
    assert [h.trigger_transaction_id for h in found] == [UUID(int=103), UUID(int=112)]


class _CountingStore(MemoryAggregateStore):
    """A memory store that remembers which parties it was fed."""

    made: ClassVar[list["_CountingStore"]] = []

    def __init__(self) -> None:
        super().__init__()
        self.parties: set[UUID] = set()
        _CountingStore.made.append(self)

    @override
    def add(
        self, txn: EnrichedTransaction, declarations: Sequence[AggregateDeclaration]
    ) -> None:
        self.parties.add(txn.party_id)
        super().add(txn, declarations)


def test_replay_keeps_one_partys_memory_at_a_time(
    stage: Stage, monkeypatch: pytest.MonkeyPatch
):
    first = add_relationship(stage.tenant, "cust-a", "N3")
    second = add_relationship(stage.tenant, "cust-b", "N3")
    # Two near deposits each, interleaved; only cust-a gets a third.
    items = [
        deposit("a-1", local(7), "3600.00", customer_ref="cust-a"),
        deposit("b-1", local(7, 11), "3600.00", customer_ref="cust-b"),
        deposit("a-2", local(8), "3700.00", customer_ref="cust-a"),
        deposit("b-2", local(8, 11), "3700.00", customer_ref="cust-b"),
        deposit("a-3", local(9), "3800.00", customer_ref="cust-a"),
    ]
    _ = feed(stage, items)
    [stored] = hits(stage, CASH)
    _CountingStore.made.clear()
    monkeypatch.setattr(engine, "MemoryAggregateStore", _CountingStore)

    with tenant_session(system_ctx(stage.tenant_id)) as s:
        ruleset = RuleSet(
            on=local(9).date(),
            product=None,
            rules=(library_rule(CASH),),
            lists=lists(),
        )
        found = list(
            replay_rule_set(
                s,
                ruleset,
                range_start=local(1).date(),
                range_end=local(13).date(),
                page=1,
            )
        )

    assert [(h.relationship_id, h.dedup_key) for h in found] == [
        (first, stored.dedup_key)
    ]
    assert stored.relationship_id == first != second
    assert len(_CountingStore.made) == 2
    assert all(len(store.parties) == 1 for store in _CountingStore.made)


def test_replay_reads_only_processed_rows_in_the_range(stage: Stage):
    _ = add_relationship(stage.tenant, "cust-a", "N3")
    items = [
        deposit(f"a-{n}", local(day), "3600.00", customer_ref="cust-a")
        for n, day in enumerate((5, 7, 8, 9))
    ]
    _ = feed(stage, items)
    ruleset = RuleSet(
        on=local(9).date(), product=None, rules=(library_rule(CASH),), lists=lists()
    )
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        # The 5th is outside the range, so only the 9th makes three near deposits
        # (processing fired on the 8th, with the 5th).
        inside = list(
            replay_rule_set(
                s,
                ruleset,
                range_start=local(6).date(),
                range_end=local(9).date(),
                page=100,
            )
        )
        # Without the 9th only two remain in range.
        short = list(
            replay_rule_set(
                s,
                ruleset,
                range_start=local(6).date(),
                range_end=local(8).date(),
                page=100,
            )
        )
    assert [h.window_end for h in inside] == [local(9)]
    assert short == []


COUNTERPARTY = {"kind": "PERSON", "name": "Juan Pérez", "country": "MX"}


def _statements(stage: Stage) -> list[str]:
    ruleset = RuleSet(
        on=local(9).date(), product=None, rules=(library_rule(CASH),), lists=lists()
    )
    executed: list[str] = []

    def count(*args: object) -> None:
        executed.append(str(args[2]))

    with tenant_session(system_ctx(stage.tenant_id)) as s:
        bind = s.connection().engine
        event.listen(bind, "before_cursor_execute", count)
        try:
            _ = list(
                replay_rule_set(
                    s,
                    ruleset,
                    range_start=local(1).date(),
                    range_end=local(9).date(),
                    page=100,
                )
            )
        finally:
            event.remove(bind, "before_cursor_execute", count)
    return executed


def test_a_page_reads_a_bounded_number_of_queries(stage: Stage):
    # One party, three deposits a day from one counterparty: the counterparty is
    # read once, each policy snapshot once, the profile once per relationship,
    # whatever the number of rows.
    _ = add_relationship(stage.tenant, "cust-a", "N3")
    items = [
        {
            **deposit(
                f"a-{day}-{hour}", local(day, hour), "100.00", customer_ref="cust-a"
            ),
            "counterparty": COUNTERPARTY,
        }
        for day in range(1, 4)
        for hour in (9, 10, 11)
    ]
    _ = feed(stage, items)
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        snapshots = set(s.scalars(select(Transaction.policy_snapshot_id)))
        assert s.scalar(select(func.count()).select_from(Transaction)) == 9

    executed = _statements(stage)

    def reads(table: str) -> int:
        return sum(
            f"FROM {table} " in sql or sql.endswith(f"FROM {table}") for sql in executed
        )

    # The page, then the empty page that ends the walk.
    assert reads("transaction") == 2
    assert reads("counterparty") == 1
    assert reads("policy_snapshot") == len(snapshots) < 9
    assert reads("customer_relationship") == 1
    assert reads("screening_subject") == 1
    assert len(executed) == 2 + 1 + len(snapshots) + 4
