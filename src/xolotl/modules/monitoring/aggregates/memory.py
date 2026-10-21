"""The in-memory ``AggregateStore``: simulation, the decide path and tests (§8.1).

It keeps the buckets ``agg_bucket`` would hold in dictionaries and each bucket's
distinct members as a set of hashes, so a stream added here reads exactly as it
reads from Postgres (the contract test). The decide path seeds it from
``PostgresAggregateStore.buckets`` before adding the candidate (reconciliation
finding 9), and passes the ids of declarations still being backfilled as
``incomplete_ids`` so it reads them as processing does.

Pure: no database, no web framework and no other module (contract
``monitoring-core-is-pure``).
"""

import hashlib
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import replace
from datetime import datetime

from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    WindowKind,
)
from xolotl.modules.monitoring.aggregates.store import (
    AggregateKey,
    AggregateValue,
    BucketRow,
    Contribution,
    combine,
    contributions,
)
from xolotl.modules.monitoring.aggregates.windows import (
    accum_window,
    bucket_kind_for,
    bucket_start_for,
    period_bucket_start,
    window_bounds,
)
from xolotl.modules.monitoring.enums import BucketKind
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction, KeyKind

type _Series = dict[datetime, BucketRow]


def _sha256(member: str) -> bytes:
    return hashlib.sha256(member.encode()).digest()


def _grown(
    row: BucketRow | None,
    contribution: Contribution,
    *,
    start: datetime,
    end: datetime | None,
    at: datetime,
    member: bytes | None,
) -> BucketRow:
    measure = contribution.measure
    members = frozenset[bytes]() if member is None else frozenset({member})
    if row is None:
        return BucketRow(
            bucket_kind=contribution.bucket_kind,
            bucket_start=start,
            bucket_end=end,
            value=measure,
            count=1,
            max_value=measure,
            min_value=measure,
            first_at=at,
            last_at=at,
            members=members,
        )
    return replace(
        row,
        value=row.value + measure,
        count=row.count + 1,
        max_value=measure if row.max_value is None else max(row.max_value, measure),
        min_value=measure if row.min_value is None else min(row.min_value, measure),
        first_at=at if row.first_at is None else min(row.first_at, at),
        last_at=at if row.last_at is None else max(row.last_at, at),
        members=row.members | members,
    )


def _accumulator(series: _Series, at: datetime) -> BucketRow | None:
    # The latest accumulator holding ``at``, as Postgres orders it; two overlap only
    # after a split that marked the declaration incomplete.
    held = [
        row
        for row in series.values()
        if row.bucket_kind is BucketKind.ACCUM
        and row.bucket_end is not None
        and row.bucket_start <= at < row.bucket_end
    ]
    if not held:
        return None
    return max(held, key=lambda row: row.bucket_start)


def _later_accumulator(
    series: _Series, start: datetime, end: datetime
) -> BucketRow | None:
    return next(
        (
            row
            for row in series.values()
            if row.bucket_kind is BucketKind.ACCUM and start < row.bucket_start < end
        ),
        None,
    )


class MemoryAggregateStore:
    """Buckets keyed ``(kind, key, agg_id)`` then ``bucket_start``, in dictionaries.

    ``member_hash`` stands in for the tenant's blind index; a read of a
    declaration whose ``agg_id`` is in ``incomplete_ids`` has ``complete`` False.
    """

    def __init__(
        self,
        *,
        member_hash: Callable[[str], bytes] = _sha256,
        incomplete_ids: Collection[str] = (),
    ) -> None:
        self._member_hash: Callable[[str], bytes] = member_hash
        self._incomplete: frozenset[str] = frozenset(incomplete_ids)
        self._series: dict[tuple[KeyKind, str, str], _Series] = {}

    def _series_of(self, key: AggregateKey, agg_id: str) -> _Series:
        return self._series.setdefault((key.kind, key.key, agg_id), {})

    def seed(
        self,
        key: AggregateKey,
        declaration: AggregateDeclaration,
        buckets: Iterable[BucketRow],
    ) -> None:
        """Load stored buckets of one declaration for one key, replacing any held."""
        series = self._series_of(key, declaration.agg_id())
        for bucket in buckets:
            series[bucket.bucket_start] = bucket

    def add(
        self, txn: EnrichedTransaction, declarations: Sequence[AggregateDeclaration]
    ) -> None:
        """Add ``txn`` to every bucket of every declaration it counts towards."""
        at = txn.occurred_at
        for contribution in contributions(txn, declarations):
            series = self._series_of(contribution.key, contribution.agg_id)
            end: datetime | None = None
            if contribution.bucket_kind is BucketKind.ACCUM:
                open_row = _accumulator(series, at)
                if open_row is None:
                    start, end = accum_window(at, contribution.declaration.window)
                    self._move_earlier(series, contribution.agg_id, start, end)
                else:
                    start, end = open_row.bucket_start, open_row.bucket_end
            else:
                start = bucket_start_for(contribution.bucket_kind, at)
            member = contribution.member
            series[start] = _grown(
                series.get(start),
                contribution,
                start=start,
                end=end,
                at=at,
                member=None if member is None else self._member_hash(member),
            )

    def _move_earlier(
        self, series: _Series, agg_id: str, start: datetime, end: datetime
    ) -> None:
        """Start a later accumulator at ``start`` when a late row opens one before it.

        A later accumulator whose operations all fall before ``end`` joins the new
        window (spec §10.5: the window starts earlier). One with an operation at or
        after ``end`` cannot be split here, so the declaration reads incomplete.
        """
        later = _later_accumulator(series, start, end)
        if later is None:
            return
        if later.last_at is not None and later.last_at < end:
            del series[later.bucket_start]
            series[start] = replace(later, bucket_start=start, bucket_end=end)
            return
        self._incomplete |= {agg_id}

    def _value(
        self, declaration: AggregateDeclaration, rows: list[BucketRow]
    ) -> AggregateValue:
        distinct = None
        if declaration.function is AggregateFunction.DISTINCT_COUNT:
            distinct = len(frozenset[bytes]().union(*(row.members for row in rows)))
        complete = declaration.agg_id() not in self._incomplete
        return combine(rows, distinct=distinct, complete=complete)

    def read(
        self, key: AggregateKey, declaration: AggregateDeclaration, *, at: datetime
    ) -> AggregateValue:
        """Sum the buckets of the window ending at ``at``.

        Returns:
            The window's value; an ``ACCUM`` declaration reads its open window.

        """
        if declaration.window_kind is WindowKind.ACCUM:
            return self.accumulator_read(key, declaration, at=at)
        first, last = window_bounds(declaration, at=at)
        series = self._series_of(key, declaration.agg_id())
        rows = [row for start, row in series.items() if first <= start <= last]
        return self._value(declaration, rows)

    def calendar_read(
        self, key: AggregateKey, declaration: AggregateDeclaration, *, period_key: str
    ) -> AggregateValue:
        """Read the one calendar bucket ``period_key`` names.

        Returns:
            The bucket's value, or an empty one.

        """
        start = period_bucket_start(bucket_kind_for(declaration), period_key)
        row = self._series_of(key, declaration.agg_id()).get(start)
        return self._value(declaration, [] if row is None else [row])

    def accumulator_read(
        self, key: AggregateKey, declaration: AggregateDeclaration, *, at: datetime
    ) -> AggregateValue:
        """Read the accumulation window holding ``at``.

        Returns:
            The window's value, or an empty one when none holds ``at``.

        """
        row = _accumulator(self._series_of(key, declaration.agg_id()), at)
        return self._value(declaration, [] if row is None else [row])
