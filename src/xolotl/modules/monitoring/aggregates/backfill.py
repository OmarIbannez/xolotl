"""The ``monitoring.agg_backfill`` job: a new declaration's history (spec §8.4).

A declaration ``ensure_declarations`` adds, or one a late row could not split, is
incomplete (``backfilled_through`` NULL) until this job rebuilds it. A round:

1. **Start**, in one transaction: take the tenant's aggregate epoch lock
   (``monitoring.agg_epoch:<tenant_id>``) exclusively. Every drain row transaction
   holds it shared from just after the party's lock to its commit, so the start
   waits until each row transaction already running has committed, and no row
   transaction of the tenant starts until the start commits; other tenants never
   wait. Under it: take the cutoff instant, note the declaration row's version
   (``xmin``) and delete the declaration's buckets and members. The start runs
   with ``lock_timeout`` at ``START_LOCK_TIMEOUT``, so a busy tenant holds its
   drains back for at most that long; a start that times out defers the job.
2. **Walk** the tenant's ``PROCESSED`` rows that occurred in the last six months
   and were processed before the cutoff, in ``(occurred_at, id)`` pages of
   ``monitoring_agg_backfill_batch``, each page one transaction, adding each row
   to this declaration only. A row processed after the cutoff was processed by a
   row transaction that took the epoch lock after the start committed and then
   read the tenant's declarations again (``declarations_version``), so it saw
   this declaration and added itself: no row counts twice and none is missed,
   even one of a drain page that loaded its declarations before this one existed.
   Within a page the rows are added key by key, each key's rows in
   ``occurred_at`` order: a page then takes its bucket locks in a fixed order, and
   accumulators still see each key's operations in order.
3. **Finish**: set ``backfilled_through`` only if the declaration row is still the
   version noted at the start. A late row that could not split an accumulator
   meanwhile set it NULL again (its own backfill enqueue was dropped, since this
   job holds the key), so the round starts over; so does a walk whose own add
   met such a split.

A crash leaves ``backfilled_through`` NULL and the retry starts from the deletion,
so pages are never added twice. Two runs of one declaration (a reaped run still
going beside its successor) take turns on a session-level advisory lock; the
second finds the declaration complete and does nothing. A declaration already
complete, or gone, is a no-op. A run whose start timed out, or whose rounds kept
restarting, is deferred: it enqueues a follow-up job (payload ``deferrals`` one
higher, ``run_after`` ``retry_delay(deferrals)`` away) and ends ``DONE``, so a busy
tenant does not burn the job's attempts.
"""

import calendar
import logging
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID

from psycopg.errors import LockNotAvailable
from sqlalchemy import (
    Engine,
    Text,
    delete,
    func,
    literal_column,
    select,
    text,
    tuple_,
    type_coerce,
)
from sqlalchemy import update as sa_update
from sqlalchemy.exc import OperationalError
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
from xolotl.modules.monitoring.kinds import AGG_BACKFILL_KIND
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction
from xolotl.platform import clock
from xolotl.platform.config import Settings
from xolotl.platform.db import SessionFactory, tenant_session
from xolotl.platform.jobs.queue import ClockFn, Job, PostgresJobQueue, retry_delay
from xolotl.platform.tenancy.context import TenantContext

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

logger = logging.getLogger(__name__)

HISTORY_MONTHS = 6
# A round that keeps meeting splits defers the job instead of walking again.
MAX_ROUNDS = 3
# How long a start waits for the tenant's running row transactions.
START_LOCK_TIMEOUT = "5s"
_TURN = text("SELECT pg_advisory_lock(hashtextextended(:key, 0))")
_END_TURN = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")
_EPOCH = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
_EPOCH_SHARED = text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key, 0))")
_SET_LOCK_TIMEOUT = text("SELECT set_config('lock_timeout', :value, true)")
_VERSION = type_coerce(literal_column("agg_declaration.xmin::text"), Text())

# The tenant's declarations as a drain last read them: how many, and the latest
# ``created_at``. Declarations are only ever added, so a new one changes the count.
type DeclarationsVersion = tuple[int, datetime | None]


def _epoch_key(tenant_id: UUID) -> dict[str, str]:
    return {"key": f"monitoring.agg_epoch:{tenant_id}"}


def share_aggregate_epoch(session: Session, tenant_id: UUID) -> None:
    """Hold the tenant's aggregate epoch lock, shared, until the transaction ends.

    Every drain row transaction takes it before it reads the declarations again
    (``declarations_version``) and adds to them; a backfill start takes it
    exclusively, so a row is either committed before the start's cutoff or sees
    the started declaration.
    """
    _ = session.execute(_EPOCH_SHARED, _epoch_key(tenant_id))


def declarations_version(session: Session) -> DeclarationsVersion:
    """Read the cheap version of the tenant's declarations.

    Returns:
        How many declarations exist, and the latest ``created_at``.

    """
    count, latest = session.execute(
        select(func.count(), func.max(AggDeclaration.created_at))
    ).one()
    return int(count), latest


@dataclass(frozen=True, slots=True)
class _Round:
    declaration: AggregateDeclaration
    version: str
    since: datetime
    cutoff: datetime


class _StartTimedOutError(Exception):
    """The tenant's row transactions held the epoch lock past the start's timeout."""


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
    """The handler of ``monitoring.agg_backfill``: payload ``{"agg_id"}``.

    A deferred run's follow-up also carries ``deferrals``.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        settings: Settings,
        queue: PostgresJobQueue,
        clock_fn: ClockFn = clock.now,
    ) -> None:
        self._session_factory: SessionFactory = session_factory
        self._settings: Settings = settings
        self._queue: PostgresJobQueue = queue
        self._clock: ClockFn = clock_fn

    @contextmanager
    def _turn(self, key: str) -> Generator[None]:
        """Hold a session-level advisory lock on a connection of its own.

        The connection runs in autocommit, so no transaction stays open while the
        rounds run; the lock is released when the block ends, or with the
        connection if the process dies.

        Yields:
            Nothing; the lock is held inside the block.

        """
        with self._session_factory() as probe:
            bind = probe.get_bind()
        engine = bind if isinstance(bind, Engine) else bind.engine
        with engine.connect() as conn:
            auto = conn.execution_options(isolation_level="AUTOCOMMIT")
            _ = auto.execute(_TURN, {"key": key})
            try:
                yield
            finally:
                _ = auto.execute(_END_TURN, {"key": key})

    def _take_start(self, ctx: TenantContext, agg_id: str) -> _Round | None:
        with tenant_session(ctx, factory=self._session_factory) as s:
            _ = s.execute(_SET_LOCK_TIMEOUT, {"value": START_LOCK_TIMEOUT})
            _ = s.execute(_EPOCH, _epoch_key(ctx.require_tenant_id()))
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

    def _start(self, ctx: TenantContext, agg_id: str) -> _Round | None:
        """Hold the epoch lock, note the cutoff and version, delete the buckets.

        Returns:
            The round, or None when the declaration is complete or gone.

        Raises:
            _StartTimedOutError: When the tenant's row transactions held the epoch
                lock past ``START_LOCK_TIMEOUT``.
            OperationalError: When the start failed for any other reason.

        """
        try:
            return self._take_start(ctx, agg_id)
        except OperationalError as error:
            if isinstance(error.orig, LockNotAvailable):
                raise _StartTimedOutError from error
            raise

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

    def _rounds(self, ctx: TenantContext, agg_id: str) -> int | None:
        """Run rounds until the declaration is complete or gone, or too many ran.

        Returns:
            How many rows the last round walked (0 when there was nothing to do),
            or None when splits kept restarting the walk.

        """
        walked = 0
        restarts = 0
        while (step := self._start(ctx, agg_id)) is not None:
            if restarts == MAX_ROUNDS:
                logger.warning(
                    "aggregate backfill deferred: its rounds kept restarting",
                    extra={"agg_id": agg_id, "restarts": restarts},
                )
                return None
            result = self._walk(ctx, step)
            if result is not None:
                walked = result
                if self._finish(ctx, step):
                    continue
            restarts += 1
            logger.info(
                "aggregate backfill restarts after a split",
                extra={"agg_id": agg_id, "restarts": restarts},
            )
        return walked

    def backfill(self, tenant_id: UUID, agg_id: str, *, actor: str) -> int | None:
        """Rebuild one declaration's buckets from history and mark it complete.

        Returns:
            How many rows the last round walked (0 when there was nothing to do),
            or None when the run is deferred: its start timed out, or splits kept
            restarting the walk.

        """
        ctx = TenantContext.system(tenant_id, actor)
        with self._turn(f"monitoring.agg_backfill:{tenant_id}:{agg_id}"):
            try:
                return self._rounds(ctx, agg_id)
            except _StartTimedOutError:
                logger.info(
                    "aggregate backfill deferred: the tenant's drains held the start",
                    extra={"agg_id": agg_id},
                )
                return None

    def run(self, session: Session, job: Job) -> None:
        """Rebuild the job's declaration; each round and page commits on its own.

        A deferred run enqueues its follow-up in the job's session.
        """
        agg_id = str(job.payload["agg_id"])
        walked = self.backfill(job.tenant_id, agg_id, actor=f"job:{job.id}")
        if walked is not None:
            logger.info(
                "aggregate backfill ran", extra={"agg_id": agg_id, "walked": walked}
            )
            return
        deferrals = int(str(job.payload.get("deferrals", 0))) + 1
        _ = self._queue.enqueue(
            session,
            kind=AGG_BACKFILL_KIND,
            payload={"agg_id": agg_id, "deferrals": deferrals},
            key=f"agg_backfill:{agg_id}:after:{job.id}",
            run_after=self._clock() + retry_delay(deferrals),
        )
