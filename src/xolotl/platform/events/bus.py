"""The transactional outbox: publish, dispatch and replay (spec §9.2, program D8).

``publish`` inserts an ``outbox`` row in the publisher's own transaction, so an event
exists exactly when the change that caused it commits. ``dispatch_pending`` then hands
each due row to its subscribers:

* **Claiming.** Per tenant, under that tenant's row-level security, a dispatcher
  locks the oldest due rows with ``FOR UPDATE SKIP LOCKED`` and leases them for
  ``LEASE`` by setting ``locked_until``; the claim commits before any handler runs,
  so competing dispatchers skip leased rows instead of waiting on them. Due means
  ``PENDING``, older than a one-second grace window and not leased. The grace window
  is a heuristic: it gives a slower transaction that took an earlier ``seq`` about a
  second to commit before a later row is claimed, but ``created_at`` is when the
  publisher's transaction began, so one that stays open longer can still be passed.
* **Per-key order.** Only the oldest ``PENDING`` row of a key can be claimed,
  retrying and leased rows included, so a key's events reach each consumer one at a
  time in publication order. The claim statement joins each row to the oldest
  ``PENDING`` ``seq`` of its key: one pass over the tenant's pending rows however
  long one key's queue is, and rows waiting behind a key cannot crowd other keys out
  of the batch. A ``DEAD`` row stops blocking its key.
* **Exactly-once consumption.** Each consumer runs in a transaction of its own that
  first inserts its ``event_consumption`` row and then calls the handler; the two
  commit together. A competing or later delivery blocks on that insert, then finds
  the row and skips the handler, whether it comes from a lease that expired during a
  slow handler or from a crash between the handlers and the status update. When the
  handler returns, the transaction must still hold the row it inserted: a handler
  that rolled back, or swallowed a database error and so left the transaction
  failed, would otherwise have its commit turn into a rollback and the event be
  published without the consumer having it. That counts as a failure.
* **Retries.** Consumers are independent: a failing one does not stop the others in
  the same pass, and they are not run again once they have succeeded. Any failure
  counts one attempt for the event and backs it off ``min(2 ** attempts, 60)``
  seconds; the attempt that reaches ``max_attempts`` makes it ``DEAD``.
* **Publication.** The row becomes ``PUBLISHED`` when every consumer subscribed to
  its type has handled it.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import (
    Select,
    any_,
    delete,
    exists,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.platform import clock
from xolotl.platform.db import SessionFactory, bound_context, tenant_session
from xolotl.platform.enums import OutboxStatus
from xolotl.platform.errors import (
    ContextMissingError,
    ValidationFailedError,
    XolotlError,
)
from xolotl.platform.events.event import Event, event_from_row, row_from_event
from xolotl.platform.events.models import EventConsumption, OutboxRow
from xolotl.platform.events.registry import Handler, consumers, subscribers_for
from xolotl.platform.logging import safe_exception_summary
from xolotl.platform.tenancy.context import TenantContext
from xolotl.platform.tenancy.tenants import tenant_ids

logger = logging.getLogger(__name__)

DISPATCHER = "dispatcher"  # the SYSTEM actor id of dispatch and of its handlers
REPLAYER = "replay"  # the SYSTEM actor id of replayed handlers
GRACE = timedelta(seconds=1)  # an event is due once it is older than this
LEASE = timedelta(seconds=60)  # how long a claim keeps other dispatchers away
MAX_BACKOFF_SECONDS = 60
MAX_ERROR_LENGTH = 1000  # ``outbox.last_error`` never holds more than this


class ConsumptionLostError(RuntimeError):
    """A handler returned, but its transaction no longer holds its consumption row.

    The handler rolled the transaction back, or swallowed a database error and left
    the transaction failed. Committing would silently drop everything the handler
    did and the record that it ran.
    """


class InvalidStoredEventError(XolotlError):
    """A stored outbox row no longer reads back as an event."""

    status: ClassVar[int] = 500
    default_code: ClassVar[str] = "INVALID_STORED_EVENT"
    default_detail: ClassVar[str] = "a stored event no longer validates"


@dataclass(frozen=True, slots=True)
class _Claimed:
    """A leased row, read while it was locked."""

    id: UUID
    event: Event | None  # None when the stored payload no longer validates
    invalid: str  # why ``event`` is None


def claim_statement(tenant_id: UUID, batch: int) -> Select[OutboxRow]:
    # The oldest PENDING row of each key. Only that row may be claimed, so a key's
    # events go out one at a time in publication order.
    #
    # The set of those rows is one array, built once by an uncorrelated subquery (an
    # InitPlan), so the claim reads the pending rows a single time however many
    # candidates there are. Both ways of writing it as a relation get slow: a
    # correlated NOT EXISTS compares every pending row of a key with every other,
    # quadratic in the backlog, and a join to the grouped subquery is quadratic too
    # whenever the planner, going by statistics taken while nothing was pending,
    # expects that subquery to return one row and runs it again for each candidate.
    heads = (
        select(func.min(OutboxRow.seq))
        .where(
            OutboxRow.tenant_id == tenant_id,
            OutboxRow.status == OutboxStatus.PENDING,
        )
        .group_by(OutboxRow.key)
        .scalar_subquery()
    )
    return (
        select(OutboxRow)
        .where(
            OutboxRow.tenant_id == tenant_id,
            OutboxRow.status == OutboxStatus.PENDING,
            OutboxRow.seq == any_(func.array(heads)),
            OutboxRow.created_at < func.now() - GRACE,
            or_(OutboxRow.locked_until.is_(None), OutboxRow.locked_until < func.now()),
        )
        .order_by(OutboxRow.seq)
        .limit(batch)
        .with_for_update(skip_locked=True, of=OutboxRow)
    )


def _claimed(row: OutboxRow) -> _Claimed:
    try:
        return _Claimed(row.id, event_from_row(row), "")
    except ValidationError as error:
        logger.exception(
            "stored event is invalid",
            extra={"event_id": str(row.id), "tenant_id": str(row.tenant_id)},
        )
        reason = f"invalid stored event: {error.error_count()} validation error(s)"
        return _Claimed(row.id, None, reason)


def _stored_event(row: OutboxRow) -> Event:
    try:
        return event_from_row(row)
    except ValidationError as error:
        msg = (
            f"stored event {row.id} no longer validates "
            f"({error.error_count()} errors) and cannot be replayed"
        )
        raise InvalidStoredEventError(msg) from error


def _record_consumption(
    session: Session, event: Event, consumer: str
) -> datetime | None:
    """Insert the consumption row, waiting for any competing insert to finish.

    Returns:
        The row's ``consumed_at`` when this call made it, so the handler has not run
        yet; ``None`` when the consumer already handled the event.

    """
    return session.execute(
        insert(EventConsumption)
        .values(tenant_id=event.tenant_id, consumer=consumer, event_id=event.id)
        .on_conflict_do_nothing(index_elements=["tenant_id", "consumer", "event_id"])
        .returning(EventConsumption.consumed_at)
    ).scalar_one_or_none()


def _require_consumption(
    session: Session, event: Event, consumer: str, recorded: datetime
) -> None:
    """Fail unless the transaction still holds the row ``recorded`` made.

    Raises:
        ConsumptionLostError: When the row is gone, or is an older one that a
            rollback brought back.

    """
    present = session.scalar(
        select(
            exists().where(
                EventConsumption.tenant_id == event.tenant_id,
                EventConsumption.consumer == consumer,
                EventConsumption.event_id == event.id,
                EventConsumption.consumed_at == recorded,
            )
        )
    )
    if not present:
        msg = "handler returned without its consumption row"
        raise ConsumptionLostError(msg)


def _describe(consumer: str, error: Exception) -> str:
    # ``last_error`` is a plain column outside the log redaction, so the text goes
    # through the kernel's one rule for stored exception text: a database error, or
    # anything built from one, keeps its class and SQLSTATE; a validation error its
    # count; any other error its message, cut short and cleaned of what Postgres
    # cannot store. The log record carries the whole chain through the redacting
    # formatter.
    return f"{consumer}: {safe_exception_summary(error)}"


def _as_utc(moment: datetime) -> datetime:
    # A naive datetime is read as UTC; an aware one is kept as supplied.
    return moment if moment.utcoffset() is not None else moment.replace(tzinfo=UTC)


class OutboxEventBus:
    """The ``EventBus`` port backed by the ``outbox`` table.

    The bus holds no state besides its configuration, so one instance may serve any
    number of threads, and so may a dispatcher per process. ``clock_fn`` supplies
    ``published_at``; leases and backoffs use the database clock, which is also what
    decides whether they have expired.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        max_attempts: int = 10,
        clock_fn: Callable[[], datetime] = clock.now,
    ) -> None:
        self._session_factory: SessionFactory = session_factory
        self._max_attempts: int = max_attempts
        self._clock_fn: Callable[[], datetime] = clock_fn

    # ---- publish ----------------------------------------------------------------

    @staticmethod
    def publish(session: Session, event: Event) -> None:
        """Insert the event into the caller's transaction.

        The row is invisible to dispatchers until that transaction commits and
        disappears if it rolls back. The event's ``time`` is stored as supplied; a
        naive one is read as UTC, so the stored time is always an aware instant.

        Raises:
            ContextMissingError: When the session is not bound to the event's tenant.

        """
        bound = bound_context(session).require_tenant_id()
        if bound != event.tenant_id:
            msg = (
                f"session is bound to tenant {bound}, "
                f"cannot publish for tenant {event.tenant_id}"
            )
            raise ContextMissingError(msg)
        aware = event.model_copy(update={"time": _as_utc(event.time)})
        session.add(row_from_event(aware))
        session.flush()

    # ---- dispatch ---------------------------------------------------------------

    def dispatch_pending(self, *, batch: int = 200) -> int:
        """Hand due events to their subscribers, one tenant at a time.

        ``batch`` bounds the events claimed per tenant, so a busy tenant cannot keep
        the others waiting. Handler failures are recorded on the event and retried;
        anything else, such as a database error while bookkeeping, propagates and
        leaves the claimed events leased until the lease expires.

        Returns:
            The number of events handled: published, scheduled for retry or dead.

        """
        handled = 0
        for tenant_id in tenant_ids(self._session_factory, actor_id=DISPATCHER):
            ctx = TenantContext.system(tenant_id, DISPATCHER)
            for claimed in self._claim(ctx, batch):
                self._settle(ctx, claimed.id, self._consume(ctx, claimed))
                handled += 1
        return handled

    def _claim(self, ctx: TenantContext, batch: int) -> list[_Claimed]:
        tenant_id = ctx.require_tenant_id()
        with tenant_session(ctx, factory=self._session_factory) as s:
            rows = s.scalars(claim_statement(tenant_id, batch)).all()
            if rows:
                _ = s.execute(
                    update(OutboxRow)
                    .where(
                        OutboxRow.tenant_id == tenant_id,
                        OutboxRow.id.in_([row.id for row in rows]),
                    )
                    .values(locked_until=func.now() + LEASE)
                    .execution_options(synchronize_session=False)
                )
            return [_claimed(row) for row in rows]

    def _consume(self, ctx: TenantContext, claimed: _Claimed) -> list[str]:
        """Run every subscriber of the event in a transaction of its own.

        Returns:
            One message per consumer that failed, empty when all succeeded.

        """
        event = claimed.event
        if event is None:
            return [claimed.invalid]
        failures: list[str] = []
        for consumer, handler in subscribers_for(event.type):
            failure = self._run(ctx, event, consumer, handler)
            if failure is not None:
                failures.append(failure)
        return failures

    def _run(
        self, ctx: TenantContext, event: Event, consumer: str, handler: Handler
    ) -> str | None:
        try:
            with tenant_session(ctx, factory=self._session_factory) as s:
                recorded = _record_consumption(s, event, consumer)
                if recorded is not None:
                    # Its own copy: the frozen model still has a mutable ``data``.
                    handler(s, event.model_copy(deep=True))
                    _require_consumption(s, event, consumer, recorded)
        except Exception as error:
            logger.exception(
                "event handler failed",
                extra={
                    "event_id": str(event.id),
                    "event_type": event.type,
                    "consumer": consumer,
                    "tenant_id": str(event.tenant_id),
                },
            )
            return _describe(consumer, error)
        return None

    def _settle(
        self, ctx: TenantContext, event_id: UUID, failures: Sequence[str]
    ) -> None:
        """Publish the event, or count a failed attempt and back off or kill it."""
        with tenant_session(ctx, factory=self._session_factory) as s:
            row = s.get(
                OutboxRow, (ctx.require_tenant_id(), event_id), with_for_update=True
            )
            if row is None or row.status is not OutboxStatus.PENDING:
                return  # a dispatcher whose lease ran out already settled it
            if not failures:
                row.status = OutboxStatus.PUBLISHED
                row.published_at = self._clock_fn()
                row.locked_until = None
                return
            row.attempts += 1
            row.last_error = "; ".join(failures)[:MAX_ERROR_LENGTH]
            if row.attempts >= self._max_attempts:
                row.status = OutboxStatus.DEAD
                row.locked_until = None
            else:
                seconds = min(1 << row.attempts, MAX_BACKOFF_SECONDS)  # 2 ** attempts
                row.locked_until = func.now() + timedelta(seconds=seconds)

    # ---- replay -----------------------------------------------------------------

    def replay(
        self,
        *,
        since: datetime,
        types: Sequence[str],
        consumer: str,
        tenant_id: UUID | None = None,
    ) -> int:
        """Run one consumer again over events that are already published or dead.

        Only ``consumer`` runs, and only for events whose type it subscribes to;
        the others have seen these events and do not see them again. A naive
        ``since`` is read as UTC. Each event is replayed in a transaction of its
        own. A handler that fails (or rolls back, or swallows a database error)
        keeps its event's earlier consumption row, the exception propagates, and the
        events replayed before it stay replayed. A stored event that no longer
        validates stops the replay the same way, with an error that names it. A
        dead event becomes ``PUBLISHED`` once every consumer subscribed to its type
        has handled it.

        Returns:
            The number of events the consumer handled again.

        Raises:
            ValidationFailedError: When ``consumer`` has no subscription.

        """
        if consumer not in consumers():
            msg = f"no subscriber is registered as consumer {consumer!r}"
            raise ValidationFailedError(msg)
        start = _as_utc(since)
        targets = (
            [tenant_id]
            if tenant_id is not None
            else tenant_ids(self._session_factory, actor_id=REPLAYER)
        )
        return sum(self._replay_tenant(tid, start, types, consumer) for tid in targets)

    def _replay_tenant(
        self, tenant_id: UUID, since: datetime, types: Sequence[str], consumer: str
    ) -> int:
        ctx = TenantContext.system(tenant_id, REPLAYER)
        with tenant_session(ctx, factory=self._session_factory) as s:
            event_ids = s.scalars(
                select(OutboxRow.id)
                .where(
                    OutboxRow.tenant_id == tenant_id,
                    OutboxRow.status.in_([OutboxStatus.PUBLISHED, OutboxStatus.DEAD]),
                    OutboxRow.created_at >= since,
                    OutboxRow.type.in_(types),
                )
                .order_by(OutboxRow.seq)
            ).all()
        return sum(self._replay_one(ctx, event_id, consumer) for event_id in event_ids)

    def _replay_one(self, ctx: TenantContext, event_id: UUID, consumer: str) -> bool:
        tenant_id = ctx.require_tenant_id()
        with tenant_session(ctx, factory=self._session_factory) as s:
            row = s.get_one(OutboxRow, (tenant_id, event_id), with_for_update=True)
            handler = dict(subscribers_for(row.type)).get(consumer)
            if handler is None:
                return False
            event = _stored_event(row)
            _ = s.execute(
                delete(EventConsumption).where(
                    EventConsumption.tenant_id == tenant_id,
                    EventConsumption.consumer == consumer,
                    EventConsumption.event_id == event_id,
                )
            )
            recorded = s.execute(
                insert(EventConsumption)
                .values(tenant_id=tenant_id, consumer=consumer, event_id=event_id)
                .returning(EventConsumption.consumed_at)
            ).scalar_one()
            handler(s, event.model_copy(deep=True))
            _require_consumption(s, event, consumer, recorded)
            self._publish_if_complete(s, row)
        return True

    def _publish_if_complete(self, session: Session, row: OutboxRow) -> None:
        if row.status is not OutboxStatus.DEAD:
            return
        handled = set(
            session.scalars(
                select(EventConsumption.consumer).where(
                    EventConsumption.tenant_id == row.tenant_id,
                    EventConsumption.event_id == row.id,
                )
            )
        )
        if {name for name, _ in subscribers_for(row.type)} <= handled:
            row.status = OutboxStatus.PUBLISHED
            row.published_at = self._clock_fn()
            row.locked_until = None
