"""Both stores read a replayed stream identically (spec §8.1, D12)."""

from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.orm import Session

from tests.modules.monitoring.support import stream
from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    DistinctOf,
    Measure,
    TxnFilter,
    WindowKind,
)
from xolotl.modules.monitoring.aggregates.memory import MemoryAggregateStore
from xolotl.modules.monitoring.aggregates.postgres import PostgresAggregateStore
from xolotl.modules.monitoring.aggregates.store import (
    AggregateKey,
    AggregateStore,
    AggregateValue,
)
from xolotl.modules.monitoring.txn.schema import (
    Direction,
    EnrichedTransaction,
    Instrument,
    KeyKind,
    Rail,
)


def _declared(
    name: str,
    function: AggregateFunction,
    measure: Measure,
    key_kind: KeyKind,
    window_kind: WindowKind,
    window: str,
    *,
    unit_key: str | None = None,
    where: TxnFilter | None = None,
    distinct_of: DistinctOf | None = None,
) -> AggregateDeclaration:
    return AggregateDeclaration(
        name=name,
        function=function,
        measure=measure,
        unit_key=unit_key,
        key_kind=key_kind,
        window_kind=window_kind,
        window=window,
        where=where or TxnFilter(),
        distinct_of=distinct_of,
    )


DECLARATIONS = (
    _declared(
        "cash_sum_p7d",
        AggregateFunction.SUM,
        Measure.AMOUNT_MINOR,
        KeyKind.PARTY,
        WindowKind.DURATION,
        "P7D",
        where=TxnFilter(instrument=frozenset({Instrument.CASH})),
    ),
    _declared(
        "count_pt24h",
        AggregateFunction.COUNT,
        Measure.ONE,
        KeyKind.ACCOUNT,
        WindowKind.DURATION,
        "PT24H",
    ),
    _declared(
        "udi_avg_p30d",
        AggregateFunction.AVG,
        Measure.UNIT_AMOUNT,
        KeyKind.RELATIONSHIP,
        WindowKind.DURATION,
        "P30D",
        unit_key="UDI/SAME_DAY",
    ),
    _declared(
        "usd_max_month",
        AggregateFunction.MAX,
        Measure.UNIT_AMOUNT,
        KeyKind.PARTY,
        WindowKind.MONTH,
        "MONTH",
        unit_key="USD/PRIOR_BANKING_DAY",
        where=TxnFilter(direction=frozenset({Direction.OUT})),
    ),
    _declared(
        "fee_min_quarter",
        AggregateFunction.MIN,
        Measure.FEE_MINOR,
        KeyKind.DEVICE,
        WindowKind.QUARTER,
        "QUARTER",
    ),
    _declared(
        "counterparties_p3m",
        AggregateFunction.DISTINCT_COUNT,
        Measure.ONE,
        KeyKind.PARTY,
        WindowKind.DURATION,
        "P3M",
        distinct_of="COUNTERPARTY",
    ),
    _declared(
        "first_crypto_accum",
        AggregateFunction.FIRST_SEEN,
        Measure.ASSET_QTY,
        KeyKind.PARTY,
        WindowKind.ACCUM,
        "P6M",
    ),
    _declared(
        "last_swift_p14d",
        AggregateFunction.LAST_SEEN,
        Measure.AMOUNT_MINOR,
        KeyKind.COUNTERPARTY,
        WindowKind.DURATION,
        "P14D",
        where=TxnFilter(rail=frozenset({Rail.SWIFT}), cross_border=True),
    ),
)


_CALENDAR: dict[WindowKind, Callable[[EnrichedTransaction], str]] = {
    WindowKind.MONTH: lambda txn: txn.period_keys.MONTH,
    WindowKind.QUARTER: lambda txn: txn.period_keys.QUARTER,
}


def _reads(
    store: AggregateStore, keys: list[AggregateKey], rows: list[EnrichedTransaction]
) -> list[AggregateValue]:
    """Read every declaration for every key of its kind at 20 instants.

    Returns:
        The reads, in a fixed order.

    """
    instants = [
        rows[index].occurred_at + timedelta(minutes=5) for index in range(0, 300, 15)
    ]
    values: list[AggregateValue] = []
    for declaration in DECLARATIONS:
        for key in (k for k in keys if k.kind is declaration.key_kind):
            values.extend(store.read(key, declaration, at=at) for at in instants)
            if declaration.window_kind is WindowKind.ACCUM:
                values.extend(
                    store.accumulator_read(key, declaration, at=at) for at in instants
                )
            if declaration.window_kind in _CALENDAR:
                values.extend(
                    store.calendar_read(
                        key,
                        declaration,
                        period_key=_CALENDAR[declaration.window_kind](txn),
                    )
                    for txn in rows[::30]
                )
    return values


def test_postgres_and_memory_stores_agree_on_a_replayed_stream(s: Session):
    rows = stream(300, seed=4)
    memory = MemoryAggregateStore()
    postgres = PostgresAggregateStore(s, complete_ids=())
    for txn in rows:
        memory.add(txn, DECLARATIONS)
        postgres.add(txn, DECLARATIONS)
    keys = sorted(
        {
            AggregateKey(kind, key)
            for txn in rows
            for kind in KeyKind
            if (key := txn.key_for(kind)) is not None
        },
        key=lambda k: (k.kind, k.key),
    )
    assert {k.kind for k in keys} == set(KeyKind)
    from_memory = [_strip(v) for v in _reads(memory, keys, rows)]
    from_postgres = [_strip(v) for v in _reads(postgres, keys, rows)]
    assert from_memory == from_postgres
    # The stream exercises every declaration: none reads empty everywhere.
    assert sum(v.count for v in from_memory) > 0
    for declaration in DECLARATIONS:
        assert any(
            memory.read(key, declaration, at=rows[-1].occurred_at).count
            or memory.accumulator_read(key, declaration, at=rows[-1].occurred_at).count
            for key in keys
            if key.kind is declaration.key_kind
        ), declaration.name


def _strip(value: AggregateValue) -> AggregateValue:
    """Drop ``complete``, which each store reports its own way.

    Returns:
        ``value`` with ``complete`` True.

    """
    return AggregateValue(
        value.value,
        value.count,
        value.max_value,
        value.min_value,
        value.first_at,
        value.last_at,
        value.distinct,
        complete=True,
    )
