"""The ``AggregateStore`` port and the values it reads (spec §8.1).

``PostgresAggregateStore`` (production and the decide path) and
``MemoryAggregateStore`` (simulation, the decide path's candidate and tests)
implement it; ``combine`` folds the buckets of a window the same way for both, so
a replayed stream reads identically through either (D12).

Pure: no database, no web framework and no other module (contract
``monitoring-core-is-pure``).
"""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    matches,
    measure_of,
    member_of,
)
from xolotl.modules.monitoring.aggregates.windows import bucket_kind_for
from xolotl.modules.monitoring.enums import BucketKind
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction, KeyKind


@dataclass(frozen=True, slots=True)
class AggregateKey:
    """What an aggregate is kept per: ``AggregateKey(kind, txn.key_for(kind))``."""

    kind: KeyKind
    key: str


@dataclass(frozen=True, slots=True)
class BucketRow:
    """One bucket of one declaration for one key; ``members`` are member hashes."""

    bucket_kind: BucketKind
    bucket_start: datetime
    bucket_end: datetime | None
    value: Decimal
    count: int
    max_value: Decimal | None
    min_value: Decimal | None
    first_at: datetime | None
    last_at: datetime | None
    members: frozenset[bytes]


@dataclass(frozen=True, slots=True)
class AggregateValue:
    """A read over a window; ``complete`` is False until the backfill has run.

    ``distinct`` is set for a ``DISTINCT_COUNT`` declaration only. The evaluator
    reads an aggregate whose ``complete`` is False as ``INCOMPLETE``.
    """

    value: Decimal
    count: int
    max_value: Decimal | None
    min_value: Decimal | None
    first_at: datetime | None
    last_at: datetime | None
    distinct: int | None
    complete: bool

    def of(self, function: AggregateFunction) -> Decimal | int | datetime | None:
        """Read the value a function asks for.

        Returns:
            The sum, count, average (None over no transaction), maximum, minimum,
            distinct count, first or last time.

        """
        if function is AggregateFunction.AVG:
            return None if self.count == 0 else self.value / self.count
        values: dict[AggregateFunction, Decimal | int | datetime | None] = {
            AggregateFunction.SUM: self.value,
            AggregateFunction.COUNT: self.count,
            AggregateFunction.MAX: self.max_value,
            AggregateFunction.MIN: self.min_value,
            AggregateFunction.DISTINCT_COUNT: self.distinct,
            AggregateFunction.FIRST_SEEN: self.first_at,
            AggregateFunction.LAST_SEEN: self.last_at,
        }
        return values[function]


EMPTY = AggregateValue(Decimal(0), 0, None, None, None, None, None, complete=True)


def _least[T: (Decimal, datetime)](values: Iterable[T | None]) -> T | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _greatest[T: (Decimal, datetime)](values: Iterable[T | None]) -> T | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def combine(
    rows: Sequence[BucketRow], *, distinct: int | None, complete: bool
) -> AggregateValue:
    """Fold the buckets of a window into one value.

    Returns:
        The sum of values and counts, the extremes of the extremes and times, the
        given distinct count and completeness.

    """
    return AggregateValue(
        value=sum((row.value for row in rows), Decimal(0)),
        count=sum(row.count for row in rows),
        max_value=_greatest(row.max_value for row in rows),
        min_value=_least(row.min_value for row in rows),
        first_at=_least(row.first_at for row in rows),
        last_at=_greatest(row.last_at for row in rows),
        distinct=distinct,
        complete=complete,
    )


@dataclass(frozen=True, slots=True)
class Contribution:
    """What one transaction adds to one declaration's bucket for its key.

    ``member`` is the distinct member of a ``DISTINCT_COUNT`` declaration.
    """

    key: AggregateKey
    declaration: AggregateDeclaration
    agg_id: str
    bucket_kind: BucketKind
    measure: Decimal
    member: str | None


def contributions(
    txn: EnrichedTransaction, declarations: Sequence[AggregateDeclaration]
) -> Iterator[Contribution]:
    """List what ``txn`` adds, one entry per declaration it counts towards.

    A declaration is skipped when its filter refuses the transaction, the row
    lacks the measure or the key, or a distinct count finds no member.

    Yields:
        One contribution per declaration ``txn`` counts towards.

    """
    for declaration in declarations:
        if not matches(declaration.where, txn):
            continue
        measure = measure_of(declaration, txn)
        key = txn.key_for(declaration.key_kind)
        member = member_of(declaration, txn)
        distinct = declaration.function is AggregateFunction.DISTINCT_COUNT
        if measure is None or key is None or (distinct and member is None):
            continue
        yield Contribution(
            key=AggregateKey(declaration.key_kind, key),
            declaration=declaration,
            agg_id=declaration.agg_id(),
            bucket_kind=bucket_kind_for(declaration),
            measure=measure,
            member=member if distinct else None,
        )


class AggregateStore(Protocol):
    """Declared aggregates per key, written per transaction and read per window."""

    def add(
        self, txn: EnrichedTransaction, declarations: Sequence[AggregateDeclaration]
    ) -> None:
        """Update every bucket ``txn`` falls in, for each declaration it matches."""
        ...

    def read(
        self, key: AggregateKey, declaration: AggregateDeclaration, *, at: datetime
    ) -> AggregateValue:
        """Sum the buckets of the window ending at ``at``."""
        ...

    def calendar_read(
        self, key: AggregateKey, declaration: AggregateDeclaration, *, period_key: str
    ) -> AggregateValue:
        """Read the one ``MONTH`` or ``QUARTER`` bucket ``period_key`` names."""
        ...

    def accumulator_read(
        self, key: AggregateKey, declaration: AggregateDeclaration, *, at: datetime
    ) -> AggregateValue:
        """Read the ``ACCUM`` window containing ``at``."""
        ...
