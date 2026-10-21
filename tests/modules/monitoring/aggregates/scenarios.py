"""Hand-computed scenarios both store adapters must read the same way.

``test_memory.py`` and ``test_postgres.py`` run each scenario against their store.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from tests.modules.monitoring.support import stream
from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    DistinctOf,
    Measure,
    TxnFilter,
    WindowKind,
)
from xolotl.modules.monitoring.aggregates.store import AggregateKey, AggregateStore
from xolotl.modules.monitoring.txn.schema import (
    Direction,
    EnrichedTransaction,
    KeyKind,
)

PARTY = UUID(int=100)
OTHER_PARTY = UUID(int=101)
KEY = AggregateKey(KeyKind.PARTY, str(PARTY))
C1, C2 = "c1" * 32, "c2" * 32
_BASE = stream(1, seed=1)[0]


def txn(at: datetime, amount_minor: int, **changes: object) -> EnrichedTransaction:
    """Return the base stream row at ``at`` for ``PARTY`` with ``changes`` applied.

    Returns:
        The transaction.

    """
    update: dict[str, object] = {
        "occurred_at": at,
        "amount_minor": amount_minor,
        "party_id": PARTY,
        "direction": Direction.IN,
        "counterparty_key": C1,
        **changes,
    }
    return _BASE.model_copy(update=update)


def declaration(
    function: AggregateFunction,
    *,
    name: str | None = None,
    measure: Measure = Measure.AMOUNT_MINOR,
    window: str = "P7D",
    where: TxnFilter | None = None,
    distinct_of: DistinctOf | None = None,
) -> AggregateDeclaration:
    """Declare a P7D party aggregate of ``function``.

    A distinct count counts counterparties unless ``distinct_of`` says otherwise.

    Returns:
        The declaration.

    """
    distinct = function is AggregateFunction.DISTINCT_COUNT
    return AggregateDeclaration(
        name=name or f"p7d_{function.lower()}",
        function=function,
        measure=measure,
        unit_key=None,
        key_kind=KeyKind.PARTY,
        window_kind=WindowKind.DURATION,
        window=window,
        where=where or TxnFilter(),
        distinct_of=distinct_of or ("COUNTERPARTY" if distinct else None),
    )


T1 = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 12, 1, 11, 30, tzinfo=UTC)
T3 = datetime(2026, 12, 2, 9, 0, tzinfo=UTC)
SMALL = (
    txn(T1, 10_000),
    txn(T2, 25_000, counterparty_key=C2),
    txn(T3, 5_000),
)
READ_AT = datetime(2026, 12, 2, 10, 0, tzinfo=UTC)
EXPECTED: dict[AggregateFunction, object] = {
    AggregateFunction.SUM: Decimal(40_000),
    AggregateFunction.COUNT: 3,
    AggregateFunction.AVG: Decimal(40_000) / 3,
    AggregateFunction.MAX: Decimal(25_000),
    AggregateFunction.MIN: Decimal(5_000),
    AggregateFunction.DISTINCT_COUNT: 2,
    AggregateFunction.FIRST_SEEN: T1,
    AggregateFunction.LAST_SEEN: T3,
}


def each_function(store: AggregateStore, function: AggregateFunction) -> object:
    """Add ``SMALL`` under a declaration of ``function`` and read it at ``READ_AT``.

    Returns:
        What the read gives for ``function``.

    """
    declared = declaration(function)
    for row in SMALL:
        store.add(row, [declared])
    return store.read(KEY, declared, at=READ_AT).of(function)


def outside_the_filter(store: AggregateStore) -> tuple[int, int]:
    """Count inbound transactions only, then add an outbound one.

    Returns:
        The count before and after the outbound transaction.

    """
    declared = declaration(
        AggregateFunction.COUNT, where=TxnFilter(direction=frozenset({Direction.IN}))
    )
    for row in SMALL:
        store.add(row, [declared])
    before = store.read(KEY, declared, at=READ_AT).count
    store.add(txn(T3, 7_000, direction=Direction.OUT), [declared])
    return before, store.read(KEY, declared, at=READ_AT).count


ACCUM = AggregateDeclaration(
    name="accum_count",
    function=AggregateFunction.COUNT,
    measure=Measure.ONE,
    unit_key=None,
    key_kind=KeyKind.PARTY,
    window_kind=WindowKind.ACCUM,
    window="P6M",
)


def accumulators(store: AggregateStore) -> list[tuple[int, datetime | None]]:
    """Open two accumulators for ``PARTY`` and one for ``OTHER_PARTY``.

    Returns:
        ``(count, first_at)`` of the reads at 2025-12-01, 2026-03-02 and 2026-07-20
        for ``PARTY``, then at 2026-03-02 for ``OTHER_PARTY``.

    """
    for at in (
        datetime(2026, 1, 10, 18, 0, tzinfo=UTC),
        datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    ):
        store.add(txn(at, 1_000), [ACCUM])
    store.add(
        txn(datetime(2026, 3, 1, 12, 0, tzinfo=UTC), 1_000, party_id=OTHER_PARTY),
        [ACCUM],
    )
    other = AggregateKey(KeyKind.PARTY, str(OTHER_PARTY))
    reads = [
        store.accumulator_read(KEY, ACCUM, at=datetime(2025, 12, 1, tzinfo=UTC)),
        store.accumulator_read(KEY, ACCUM, at=datetime(2026, 3, 2, tzinfo=UTC)),
        store.read(KEY, ACCUM, at=datetime(2026, 7, 20, tzinfo=UTC)),
        store.accumulator_read(other, ACCUM, at=datetime(2026, 3, 2, tzinfo=UTC)),
    ]
    return [(value.count, value.first_at) for value in reads]


ACCUM_EXPECTED = [
    (0, None),
    (2, datetime(2026, 1, 10, 18, 0, tzinfo=UTC)),
    (1, datetime(2026, 7, 15, 12, 0, tzinfo=UTC)),
    (1, datetime(2026, 3, 1, 12, 0, tzinfo=UTC)),
]


ACCUM_SUM = AggregateDeclaration(
    name="accum_sum",
    function=AggregateFunction.SUM,
    measure=Measure.AMOUNT_MINOR,
    unit_key=None,
    key_kind=KeyKind.PARTY,
    window_kind=WindowKind.ACCUM,
    window="P6M",
)
LATE_FIRST = datetime(2026, 7, 10, 18, 0, tzinfo=UTC)


def late_accumulator(
    store: AggregateStore,
) -> list[tuple[Decimal, int, datetime | None]]:
    """Open an accumulator on 14 July, then add a late row of 10 July and 1 August.

    The late row starts the window earlier (spec §10.5) instead of opening a second
    accumulator, so the window runs from 10 July to 10 January.

    Returns:
        ``(value, count, first_at)`` of the reads at 1 August, 11 July and
        9 January 2027 (still in the window) and 11 January 2027 (past it).

    """
    for at in (
        datetime(2026, 7, 14, 18, 0, tzinfo=UTC),
        datetime(2026, 7, 15, 18, 0, tzinfo=UTC),
        LATE_FIRST,
        datetime(2026, 8, 1, 18, 0, tzinfo=UTC),
    ):
        store.add(txn(at, 100), [ACCUM_SUM])
    reads = [
        store.accumulator_read(KEY, ACCUM_SUM, at=at)
        for at in (
            datetime(2026, 8, 1, 19, 0, tzinfo=UTC),
            datetime(2026, 7, 11, tzinfo=UTC),
            datetime(2027, 1, 9, tzinfo=UTC),
            datetime(2027, 1, 11, tzinfo=UTC),
        )
    ]
    return [(value.value, value.count, value.first_at) for value in reads]


LATE_EXPECTED = [
    (Decimal(400), 4, LATE_FIRST),
    (Decimal(400), 4, LATE_FIRST),
    (Decimal(400), 4, LATE_FIRST),
    (Decimal(0), 0, None),
]


def split_accumulator(store: AggregateStore) -> None:
    """Add rows on 14 July and 13 January, then a row of 1 July far behind them.

    The 1 July row opens a window to 1 January that the 14 July accumulator,
    holding the 13 January row, cannot join.
    """
    for at in (
        datetime(2026, 7, 14, 18, 0, tzinfo=UTC),
        datetime(2027, 1, 13, 18, 0, tzinfo=UTC),
        datetime(2026, 7, 1, 18, 0, tzinfo=UTC),
    ):
        store.add(txn(at, 100), [ACCUM_SUM])
