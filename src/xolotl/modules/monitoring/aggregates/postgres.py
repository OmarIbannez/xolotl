"""The Postgres ``AggregateStore`` and the tenant's declarations (spec §8.1, §8.4).

Buckets live in ``agg_bucket`` and distinct members, blind-indexed under the
tenant's INDEX key, in ``agg_distinct``; both are written with ``INSERT … ON
CONFLICT`` in the caller's transaction, so a transaction's contribution commits or
rolls back with the row that carries it. ``agg_declaration`` holds every
declaration processing maintains; ``ensure_declarations`` is its only writer and
enqueues the backfill of each new one, and until that backfill sets
``backfilled_through`` every read of the declaration is incomplete.

A late row that opens an accumulator before a key's open one moves that window
earlier (spec §10.5): the later bucket and its members are folded into the new
one. When the later bucket already holds an operation past the new window's end
(only a backfilled row more than ``monitoring_late_days`` late can do that), the
store cannot split it: it sets ``backfilled_through`` back to NULL and enqueues
the declaration's backfill, which rebuilds its buckets in ``occurred_at`` order.
"""

import json
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID

from sqlalchemy import DateTime, delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    WindowKind,
)
from xolotl.modules.monitoring.aggregates.models import (
    AggBucket,
    AggDeclaration,
    AggDistinct,
)
from xolotl.modules.monitoring.aggregates.store import (
    AccumulatorSplitError,
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
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction
from xolotl.platform.crypto.blind_index import blind_index
from xolotl.platform.db import bound_context
from xolotl.platform.jobs.queue import PostgresJobQueue

if TYPE_CHECKING:
    from pydantic import JsonValue

# The job that walks a new declaration's history; the value of A4's
# ``monitoring.kinds.AGG_BACKFILL_KIND``, which merges beside this task.
AGG_BACKFILL_KIND = "monitoring.agg_backfill"
_BUCKET_KEY = ("tenant_id", "key_kind", "key", "agg_id", "bucket_start")


def _row(bucket: AggBucket, members: frozenset[bytes]) -> BucketRow:
    return BucketRow(
        bucket_kind=bucket.bucket_kind,
        bucket_start=bucket.bucket_start,
        bucket_end=bucket.bucket_end,
        value=bucket.value,
        count=bucket.count,
        max_value=bucket.max_value,
        min_value=bucket.min_value,
        first_at=bucket.first_at,
        last_at=bucket.last_at,
        members=members,
    )


class PostgresAggregateStore:
    """The tenant's aggregates in ``agg_bucket`` and ``agg_distinct``.

    ``complete_ids`` fixes which declarations read as complete; when None, each
    read looks up its declaration's ``backfilled_through``. ``queue`` enqueues
    the backfill of a declaration whose accumulator a late row cannot split;
    without one such a row raises ``AccumulatorSplitError``.
    """

    def __init__(
        self,
        session: Session,
        *,
        complete_ids: Collection[str] | None = None,
        queue: PostgresJobQueue | None = None,
    ) -> None:
        self._s: Session = session
        self._complete: frozenset[str] | None = (
            None if complete_ids is None else frozenset(complete_ids)
        )
        self._queue: PostgresJobQueue | None = queue

    def _tenant(self) -> UUID:
        return bound_context(self._s).require_tenant_id()

    def _member_hash(self, member: str) -> bytes:
        return blind_index(
            "agg_member", member, tenant_id=self._tenant(), session=self._s
        )

    def _accumulator(
        self, key: AggregateKey, agg_id: str, at: datetime
    ) -> AggBucket | None:
        return self._s.scalars(
            select(AggBucket)
            .where(
                AggBucket.key_kind == key.kind,
                AggBucket.key == key.key,
                AggBucket.agg_id == agg_id,
                AggBucket.bucket_kind == BucketKind.ACCUM,
                AggBucket.bucket_start <= at,
                AggBucket.bucket_end > at,
            )
            .order_by(AggBucket.bucket_start.desc())
            .limit(1)
        ).first()

    def _later_accumulator(
        self, key: AggregateKey, agg_id: str, start: datetime, end: datetime
    ) -> AggBucket | None:
        return self._s.scalars(
            select(AggBucket)
            .where(
                AggBucket.key_kind == key.kind,
                AggBucket.key == key.key,
                AggBucket.agg_id == agg_id,
                AggBucket.bucket_kind == BucketKind.ACCUM,
                AggBucket.bucket_start > start,
                AggBucket.bucket_start < end,
            )
            .order_by(AggBucket.bucket_start)
            .limit(1)
        ).first()

    def _move_earlier(
        self, key: AggregateKey, agg_id: str, start: datetime, end: datetime
    ) -> None:
        """Fold a later accumulator into the one a late row opens at ``start``.

        A later accumulator with an operation at or after ``end`` cannot join the
        new window, so the declaration is rebuilt instead.
        """
        later = self._later_accumulator(key, agg_id, start, end)
        if later is None:
            return
        if later.last_at is None or later.last_at >= end:
            self._rebuild(agg_id)
            return
        old_start = later.bucket_start
        self._accumulate(
            key,
            agg_id,
            replace(_row(later, frozenset()), bucket_start=start, bucket_end=end),
        )
        old = (
            AggDistinct.key_kind == key.kind,
            AggDistinct.key == key.key,
            AggDistinct.agg_id == agg_id,
            AggDistinct.bucket_start == old_start,
        )
        _ = self._s.execute(
            insert(AggDistinct)
            .from_select(
                [
                    "tenant_id",
                    "key_kind",
                    "key",
                    "agg_id",
                    "bucket_start",
                    "member_hash",
                ],
                select(
                    AggDistinct.tenant_id,
                    AggDistinct.key_kind,
                    AggDistinct.key,
                    AggDistinct.agg_id,
                    literal(start, DateTime(timezone=True)),
                    AggDistinct.member_hash,
                ).where(*old),
            )
            .on_conflict_do_nothing()
        )
        _ = self._s.execute(delete(AggDistinct).where(*old))
        _ = self._s.execute(
            delete(AggBucket).where(
                AggBucket.key_kind == key.kind,
                AggBucket.key == key.key,
                AggBucket.agg_id == agg_id,
                AggBucket.bucket_start == old_start,
            )
        )

    def _rebuild(self, agg_id: str) -> None:
        """Mark the declaration incomplete and enqueue its backfill.

        Raises:
            AccumulatorSplitError: When the store has no queue to enqueue it with.

        """
        if self._queue is None:
            msg = f"aggregate {agg_id}: a late row would split an accumulator"
            raise AccumulatorSplitError(msg)
        _ = self._s.execute(
            update(AggDeclaration)
            .where(AggDeclaration.agg_id == agg_id)
            .values(backfilled_through=None)
        )
        _ = self._queue.enqueue(
            self._s,
            kind=AGG_BACKFILL_KIND,
            payload={"agg_id": agg_id},
            key=f"agg_backfill:{agg_id}",
        )
        if self._complete is not None:
            self._complete -= {agg_id}

    def _upsert(self, contribution: Contribution, at: datetime) -> None:
        key, agg_id = contribution.key, contribution.agg_id
        end: datetime | None = None
        if contribution.bucket_kind is BucketKind.ACCUM:
            open_bucket = self._accumulator(key, agg_id, at)
            if open_bucket is None:
                start, end = accum_window(at, contribution.declaration.window)
                self._move_earlier(key, agg_id, start, end)
            else:
                start, end = open_bucket.bucket_start, open_bucket.bucket_end
        else:
            start = bucket_start_for(contribution.bucket_kind, at)
        measure = contribution.measure
        self._accumulate(
            key,
            agg_id,
            BucketRow(
                bucket_kind=contribution.bucket_kind,
                bucket_start=start,
                bucket_end=end,
                value=measure,
                count=1,
                max_value=measure,
                min_value=measure,
                first_at=at,
                last_at=at,
                members=frozenset(),
            ),
        )
        if contribution.member is not None:
            _ = self._s.execute(
                insert(AggDistinct)
                .values(
                    tenant_id=self._tenant(),
                    key_kind=key.kind,
                    key=key.key,
                    agg_id=agg_id,
                    bucket_start=start,
                    member_hash=self._member_hash(contribution.member),
                )
                .on_conflict_do_nothing()
            )

    def _accumulate(self, key: AggregateKey, agg_id: str, row: BucketRow) -> None:
        """Insert ``row`` as a bucket of ``key``, or fold it into the one there."""
        statement = insert(AggBucket).values(
            tenant_id=self._tenant(),
            key_kind=key.kind,
            key=key.key,
            agg_id=agg_id,
            bucket_start=row.bucket_start,
            bucket_kind=row.bucket_kind,
            bucket_end=row.bucket_end,
            value=row.value,
            count=row.count,
            max_value=row.max_value,
            min_value=row.min_value,
            first_at=row.first_at,
            last_at=row.last_at,
        )
        new = statement.excluded
        _ = self._s.execute(
            statement.on_conflict_do_update(
                index_elements=_BUCKET_KEY,
                set_={
                    "value": AggBucket.value + new.value,
                    "count": AggBucket.count + new.count,
                    "max_value": func.greatest(AggBucket.max_value, new.max_value),
                    "min_value": func.least(AggBucket.min_value, new.min_value),
                    "first_at": func.least(AggBucket.first_at, new.first_at),
                    "last_at": func.greatest(AggBucket.last_at, new.last_at),
                    "updated_at": func.now(),
                },
            )
        )

    def add(
        self, txn: EnrichedTransaction, declarations: Sequence[AggregateDeclaration]
    ) -> None:
        """Add ``txn`` to every bucket of every declaration it counts towards."""
        for contribution in contributions(txn, declarations):
            self._upsert(contribution, txn.occurred_at)

    def _is_complete(self, agg_id: str) -> bool:
        if self._complete is not None:
            return agg_id in self._complete
        through = self._s.scalar(
            select(AggDeclaration.backfilled_through).where(
                AggDeclaration.agg_id == agg_id
            )
        )
        return through is not None

    def _range(
        self,
        key: AggregateKey,
        declaration: AggregateDeclaration,
        *,
        start: datetime,
        end: datetime,
    ) -> AggregateValue:
        agg_id = declaration.agg_id()
        buckets = self._s.scalars(
            select(AggBucket).where(
                AggBucket.key_kind == key.kind,
                AggBucket.key == key.key,
                AggBucket.agg_id == agg_id,
                AggBucket.bucket_kind == bucket_kind_for(declaration),
                AggBucket.bucket_start >= start,
                AggBucket.bucket_start <= end,
            )
        ).all()
        distinct = None
        if declaration.function is AggregateFunction.DISTINCT_COUNT:
            distinct = self._s.scalar(
                select(func.count(func.distinct(AggDistinct.member_hash))).where(
                    AggDistinct.key_kind == key.kind,
                    AggDistinct.key == key.key,
                    AggDistinct.agg_id == agg_id,
                    AggDistinct.bucket_start >= start,
                    AggDistinct.bucket_start <= end,
                )
            )
        return combine(
            [_row(bucket, frozenset()) for bucket in buckets],
            distinct=distinct,
            complete=self._is_complete(agg_id),
        )

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
        return self._range(key, declaration, start=first, end=last)

    def calendar_read(
        self, key: AggregateKey, declaration: AggregateDeclaration, *, period_key: str
    ) -> AggregateValue:
        """Read the one calendar bucket ``period_key`` names.

        Returns:
            The bucket's value, or an empty one.

        """
        start = period_bucket_start(bucket_kind_for(declaration), period_key)
        return self._range(key, declaration, start=start, end=start)

    def accumulator_read(
        self, key: AggregateKey, declaration: AggregateDeclaration, *, at: datetime
    ) -> AggregateValue:
        """Read the accumulation window holding ``at``.

        Returns:
            The window's value, or an empty one when none holds ``at``.

        """
        bucket = self._accumulator(key, declaration.agg_id(), at)
        if bucket is None:
            distinct = (
                0 if declaration.function is AggregateFunction.DISTINCT_COUNT else None
            )
            return combine(
                [], distinct=distinct, complete=self._is_complete(declaration.agg_id())
            )
        start = bucket.bucket_start
        return self._range(key, declaration, start=start, end=start)

    def buckets(
        self,
        key: AggregateKey,
        declaration: AggregateDeclaration,
        *,
        start: datetime,
        end: datetime,
    ) -> list[BucketRow]:
        """Read the stored buckets whose start lies in ``[start, end]``.

        The decide path seeds a ``MemoryAggregateStore`` with them.

        Returns:
            The buckets with their member hashes, newest first.

        """
        agg_id = declaration.agg_id()
        buckets = self._s.scalars(
            select(AggBucket)
            .where(
                AggBucket.key_kind == key.kind,
                AggBucket.key == key.key,
                AggBucket.agg_id == agg_id,
                AggBucket.bucket_kind == bucket_kind_for(declaration),
                AggBucket.bucket_start >= start,
                AggBucket.bucket_start <= end,
            )
            .order_by(AggBucket.bucket_start.desc())
        ).all()
        members: defaultdict[datetime, set[bytes]] = defaultdict(set)
        for bucket_start, member_hash in self._s.execute(
            select(AggDistinct.bucket_start, AggDistinct.member_hash).where(
                AggDistinct.key_kind == key.kind,
                AggDistinct.key == key.key,
                AggDistinct.agg_id == agg_id,
                AggDistinct.bucket_start >= start,
                AggDistinct.bucket_start <= end,
            )
        ):
            members[bucket_start].add(member_hash)
        return [
            _row(bucket, frozenset(members.get(bucket.bucket_start, ())))
            for bucket in buckets
        ]


def load_declarations(session: Session) -> list[AggregateDeclaration]:
    """Read every declaration the tenant's processing maintains.

    Each is named by its ``agg_id``: the canonical JSON keeps no name.

    Returns:
        The declarations, in ``agg_id`` order.

    """
    rows = session.scalars(select(AggDeclaration).order_by(AggDeclaration.agg_id))
    return [
        AggregateDeclaration.from_json(
            row.agg_id, cast("Mapping[str, JsonValue]", row.declaration)
        )
        for row in rows
    ]


def backfilled_ids(session: Session) -> frozenset[str]:
    """Name the declarations whose backfill has run.

    The decide path reads every other declaration as incomplete
    (``incomplete_ids``), and processing passes this set as ``complete_ids`` once
    per drain instead of a lookup per read.

    Returns:
        The ``agg_id`` of every declaration whose ``backfilled_through`` is set.

    """
    return frozenset(
        session.scalars(
            select(AggDeclaration.agg_id).where(
                AggDeclaration.backfilled_through.is_not(None)
            )
        )
    )


def ensure_declarations(
    session: Session,
    declarations: Iterable[AggregateDeclaration],
    *,
    queue: PostgresJobQueue,
) -> list[str]:
    """Record the declarations ``agg_declaration`` lacks and enqueue their backfills.

    A new row has ``backfilled_through`` NULL, so its reads are incomplete until
    ``monitoring.agg_backfill`` (key ``agg_backfill:<agg_id>``) has walked the
    history. Runs in the caller's transaction; an invalid declaration raises
    ``AggregateDeclaration.validate``'s ``ValueError`` before anything is written
    for it.

    Returns:
        The ids of the declarations this call added, in the order given.

    """
    added: list[str] = []
    for declaration in declarations:
        declaration.validate()
        agg_id = declaration.agg_id()
        inserted = session.scalar(
            insert(AggDeclaration)
            .values(
                tenant_id=bound_context(session).require_tenant_id(),
                agg_id=agg_id,
                declaration=json.loads(declaration.canonical_json()),
                bucket_kind=bucket_kind_for(declaration),
                window_kind=declaration.window_kind,
                window=declaration.window,
            )
            .on_conflict_do_nothing()
            .returning(AggDeclaration.agg_id)
        )
        if inserted is None:
            continue
        _ = queue.enqueue(
            session,
            kind=AGG_BACKFILL_KIND,
            payload={"agg_id": agg_id},
            key=f"agg_backfill:{agg_id}",
        )
        added.append(agg_id)
    return added
