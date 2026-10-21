"""The ``monitoring.agg_backfill`` job: a new declaration's history (spec §8.4).

A declaration ``ensure_declarations`` adds, or one a late row could not split, is
incomplete (``backfilled_through`` NULL) until this job rebuilds it. A round:

1. **Start**, in one transaction: lock ``agg_bucket`` and ``agg_distinct`` against
   writers (``SHARE ROW EXCLUSIVE``, held only for this transaction), so every row
   transaction that already added to them has committed and none adds until this
   one ends; take the cutoff instant; note the declaration row's version
   (``xmin``); delete the declaration's buckets and members.
2. **Walk** the tenant's ``PROCESSED`` rows that occurred in the last six months
   and were processed before the cutoff, in ``(occurred_at, id)`` pages of
   ``monitoring_agg_backfill_batch``, each page one transaction, adding each row
   to this declaration only. A row processed after the cutoff was added by its
   own drain (its ``add`` waited for the lock), so no row counts twice. Within a
   page the rows are added key by key, each key's rows in ``occurred_at`` order: a
   page then takes its bucket locks in a fixed order, and accumulators still see
   each key's operations in order.
3. **Finish**: set ``backfilled_through`` only if the declaration row is still the
   version noted at the start. A late row that could not split an accumulator
   meanwhile set it NULL again (its own backfill enqueue was dropped, since this
   job holds the key), so the round starts over; so does a walk whose own add
   met such a split.

A crash leaves ``backfilled_through`` NULL and the retry starts from the deletion,
so pages are never added twice. Two runs of one declaration (a reaped run still
going beside its successor) take turns on an advisory lock; the second finds the
declaration complete and does nothing. A declaration already complete, or gone,
is a no-op.
"""

import calendar
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID

from sqlalchemy import Text, delete, literal_column, select, text, tuple_, type_coerce
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session, defer

from xolotl.modules.monitoring.aggregates.declarations import AggregateDeclaration
from xolotl.modules.monitoring.aggregates.models import (
    AggBucket,
    AggDeclaration,
    AggDistinct,
)
from xolotl.modules.monitoring.aggregates.postgres import PostgresAggregateStore
from xolotl.modules.monitoring.aggregates.store import AccumulatorSplitError
from xolotl.modules.monitoring.enrich.service import enriched_from_row
from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction
from xolotl.platform import clock
from xolotl.platform.config import Settings
from xolotl.platform.db import SessionFactory, tenant_session
from xolotl.platform.jobs.queue import ClockFn, Job
from xolotl.platform.tenancy.context import TenantContext

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

logger = logging.getLogger(__name__)

HISTORY_MONTHS = 6
# A round that keeps meeting splits gives up and lets the queue retry the job.
MAX_ROUNDS = 3
_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
_LOCK_TABLES = text("LOCK TABLE agg_bucket, agg_distinct IN SHARE ROW EXCLUSIVE MODE")
_VERSION = type_coerce(literal_column("agg_declaration.xmin::text"), Text())


@dataclass(frozen=True, slots=True)
class _Round:
    declaration: AggregateDeclaration
    version: str
    since: datetime
    cutoff: datetime


def history_start(at: datetime) -> datetime:
    """Return where a walk at ``at`` starts: six months back, a day early, in UTC.

    The extra day covers a first bucket that starts at the day's UTC or Mexico City
    midnight.

    Returns:
        A UTC midnight.

    """
    year, month = divmod(at.year * 12 + at.month - 1 - HISTORY_MONTHS, 12)
    month += 1
    day = min(at.day, calendar.monthrange(year, month)[1])
    return datetime(year, month, day, tzinfo=UTC) - timedelta(days=1)


def _add_page(
    s: Session, rows: list[Transaction], declaration: AggregateDeclaration
) -> bool:
    """Add a page to one declaration, key by key, each key in ``occurred_at`` order.

    Returns:
        False when an add met an accumulator it cannot split.

    """
    kind = declaration.key_kind

    def order(txn: EnrichedTransaction) -> tuple[str, datetime, str]:
        return (txn.key_for(kind) or "", txn.occurred_at, str(txn.id))

    store = PostgresAggregateStore(s, complete_ids=())
    try:
        for txn in sorted((enriched_from_row(row) for row in rows), key=order):
            store.add(txn, [declaration])
    except AccumulatorSplitError:
        return False
    return True


class AggBackfillJob:
    """The handler of ``monitoring.agg_backfill``: payload ``{"agg_id"}``."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        settings: Settings,
        clock_fn: ClockFn = clock.now,
    ) -> None:
        self._session_factory: SessionFactory = session_factory
        self._settings: Settings = settings
        self._clock: ClockFn = clock_fn

    def _start(self, ctx: TenantContext, agg_id: str) -> _Round | None:
        """Lock the bucket tables, note the cutoff and version, delete the buckets.

        Returns:
            The round, or None when the declaration is complete or gone.

        """
        with tenant_session(ctx, factory=self._session_factory) as s:
            _ = s.execute(_LOCK_TABLES)
            row = s.execute(
                select(AggDeclaration.declaration, _VERSION).where(
                    AggDeclaration.agg_id == agg_id,
                    AggDeclaration.backfilled_through.is_(None),
                )
            ).one_or_none()
            if row is None:
                return None
            content, version = row
            cutoff = self._clock()
            _ = s.execute(delete(AggDistinct).where(AggDistinct.agg_id == agg_id))
            _ = s.execute(delete(AggBucket).where(AggBucket.agg_id == agg_id))
            declaration = AggregateDeclaration.from_json(
                agg_id, cast("Mapping[str, JsonValue]", content)
            )
        return _Round(declaration, version, history_start(cutoff), cutoff)

    def _page(
        self,
        s: Session,
        step: _Round,
        after: tuple[datetime, UUID] | None,
    ) -> list[Transaction]:
        page = (
            select(Transaction)
            .options(defer(Transaction.raw))
            .where(
                Transaction.status == TransactionStatus.PROCESSED,
                Transaction.occurred_at >= step.since,
                Transaction.processed_at < step.cutoff,
            )
        )
        if after is not None:
            page = page.where(tuple_(Transaction.occurred_at, Transaction.id) > after)
        return list(
            s.scalars(
                page.order_by(Transaction.occurred_at, Transaction.id).limit(
                    self._settings.monitoring_agg_backfill_batch
                )
            )
        )

    def _walk(self, ctx: TenantContext, step: _Round) -> int | None:
        """Add the history to the declaration, a page per transaction.

        Returns:
            How many rows were walked, or None when an add met a split.

        """
        walked = 0
        after: tuple[datetime, UUID] | None = None
        while True:
            with tenant_session(ctx, factory=self._session_factory) as s:
                rows = self._page(s, step, after)
                if not rows:
                    return walked
                if not _add_page(s, rows, step.declaration):
                    s.rollback()
                    return None
            walked += len(rows)
            after = (rows[-1].occurred_at, rows[-1].id)

    def _finish(self, ctx: TenantContext, step: _Round) -> bool:
        """Mark the declaration complete if nothing split it during the walk.

        Returns:
            Whether it was marked.

        """
        with tenant_session(ctx, factory=self._session_factory) as s:
            marked = s.scalar(
                sa_update(AggDeclaration)
                .where(
                    AggDeclaration.agg_id == step.declaration.agg_id(),
                    AggDeclaration.backfilled_through.is_(None),
                    _VERSION.is_not_distinct_from(step.version),
                )
                .values(backfilled_through=self._clock())
                .returning(AggDeclaration.agg_id)
            )
        return marked is not None

    def backfill(self, tenant_id: UUID, agg_id: str, *, actor: str) -> int:
        """Rebuild one declaration's buckets from history and mark it complete.

        Returns:
            How many rows the last round walked; 0 when there was nothing to do.

        Raises:
            RuntimeError: When splits kept restarting the walk; the queue retries.

        """
        ctx = TenantContext.system(tenant_id, actor)
        walked = 0
        rounds = 0
        with tenant_session(ctx, factory=self._session_factory) as guard:
            # Held, idle, until the job ends: a second run of the declaration waits.
            _ = guard.execute(
                _LOCK, {"key": f"monitoring.agg_backfill:{tenant_id}:{agg_id}"}
            )
            while (step := self._start(ctx, agg_id)) is not None:
                if rounds == MAX_ROUNDS:
                    msg = f"aggregate {agg_id}: the walk kept meeting splits"
                    raise RuntimeError(msg)
                rounds += 1
                result = self._walk(ctx, step)
                if result is None:
                    continue
                walked = result
                if not self._finish(ctx, step):
                    logger.info(
                        "aggregate backfill restarts after a split",
                        extra={"agg_id": agg_id},
                    )
        return walked

    def run(self, _session: Session, job: Job) -> None:
        """Rebuild the job's declaration; each round and page commits on its own."""
        agg_id = str(job.payload["agg_id"])
        walked = self.backfill(job.tenant_id, agg_id, actor=f"job:{job.id}")
        logger.info(
            "aggregate backfill ran", extra={"agg_id": agg_id, "walked": walked}
        )
