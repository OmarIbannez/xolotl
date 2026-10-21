"""The transaction backfill: rows held as ``BACKFILL`` processed on request (§8.5).

A row received more than ``monitoring_late_days`` after it occurred is held as
``BACKFILL`` at ingest. Staff (``POST /v1/monitoring/backfill``) or the CLI request a
backfill of a range of Mexico City days, optionally for one party; the
``monitoring.backfill`` job sets the range's ``BACKFILL`` rows ``RECEIVED`` again,
a page at a time, and drains each of their parties with the processing job's own
code, so the rows are enriched, added and evaluated in ``occurred_at`` order and
flagged late (they were received over a day after they occurred). Its aggregate
store has the job queue: a row that would split an accumulator marks the
declaration incomplete and enqueues its ``monitoring.agg_backfill`` instead of
failing. Reopening the capture periods the rows touch is plan 4b's
``CapturesHook`` (spec §10.5).

A run that stops after setting rows ``RECEIVED`` leaves them to the sweep, which
enqueues their parties like any other waiting row.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import JsonValue
from sqlalchemy import select, tuple_, update
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.errors import BackfillRangeInvalidError
from xolotl.modules.monitoring.kinds import BACKFILL_KIND
from xolotl.modules.monitoring.process.job import process_party
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.calendar import MEXICO_CITY
from xolotl.platform.config import Settings
from xolotl.platform.db import SessionFactory, tenant_session
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.queue import Job, PostgresJobQueue
from xolotl.platform.tenancy.context import TenantContext

REQUESTED_ACTION = "monitoring.backfill_requested"
_ZONE = ZoneInfo(MEXICO_CITY)


@dataclass(frozen=True, slots=True)
class BackfillReport:
    """What one backfill run did: rows released, parties drained, rows processed."""

    released: int
    parties: int
    processed: int


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=_ZONE)


def backfill_payload(
    *, from_: date, to: date, party_id: UUID | None
) -> dict[str, JsonValue]:
    """Build the ``monitoring.backfill`` payload, ids and dates only.

    Returns:
        ``{"from", "to", "party_id"}`` as strings, ``party_id`` possibly None.

    """
    return {
        "from": from_.isoformat(),
        "to": to.isoformat(),
        "party_id": None if party_id is None else str(party_id),
    }


def request_backfill(
    session: Session,
    *,
    from_: date,
    to: date,
    party_id: UUID | None,
    queue: PostgresJobQueue,
) -> UUID:
    """Audit a backfill request and enqueue its job, in the caller's transaction.

    ``from_`` and ``to`` are Mexico City days, both included.

    Returns:
        The id of the ``monitoring.backfill`` job.

    Raises:
        BackfillRangeInvalidError: When ``from_`` is after ``to`` or ``to`` is after
            today in Mexico City.
        RuntimeError: When the queue refused a job whose key is new (a defect).

    """
    today = clock.now().astimezone(_ZONE).date()
    if from_ > to:
        msg = "the range starts after it ends"
        raise BackfillRangeInvalidError(msg)
    if to > today:
        msg = "the range ends after today"
        raise BackfillRangeInvalidError(msg)
    payload = backfill_payload(from_=from_, to=to, party_id=party_id)
    job_id = queue.enqueue(
        session, kind=BACKFILL_KIND, payload=payload, key=f"backfill:{uuid7()}"
    )
    if job_id is None:
        msg = "the backfill job was not enqueued"
        raise RuntimeError(msg)
    _ = record(
        session,
        AuditEvent(
            action=REQUESTED_ACTION,
            entity_type="job",
            entity_id=str(job_id),
            detail=dict(payload),
        ),
    )
    return job_id


class BackfillJob:
    """The ``monitoring.backfill`` handler; payload ``{"from", "to", "party_id"}``."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        queue: PostgresJobQueue,
        settings: Settings,
    ) -> None:
        self._session_factory: SessionFactory = session_factory
        self._queue: PostgresJobQueue = queue
        self._settings: Settings = settings

    def _release(
        self,
        ctx: TenantContext,
        *,
        start: datetime,
        end: datetime,
        party_id: UUID | None,
    ) -> tuple[int, set[UUID]]:
        """Set the range's ``BACKFILL`` rows ``RECEIVED``, one page per transaction.

        Returns:
            How many rows were released, and their parties.

        """
        released = 0
        parties: set[UUID] = set()
        after: tuple[datetime, UUID] | None = None
        while True:
            with tenant_session(ctx, factory=self._session_factory) as s:
                page = select(
                    Transaction.occurred_at, Transaction.id, Transaction.party_id
                ).where(
                    Transaction.status == TransactionStatus.BACKFILL,
                    Transaction.occurred_at >= start,
                    Transaction.occurred_at < end,
                )
                if party_id is not None:
                    page = page.where(Transaction.party_id == party_id)
                if after is not None:
                    page = page.where(
                        tuple_(Transaction.occurred_at, Transaction.id) > after
                    )
                keys = s.execute(
                    page
                    .order_by(Transaction.occurred_at, Transaction.id)
                    .limit(self._settings.monitoring_process_page)
                    .with_for_update()
                ).all()
                if not keys:
                    return released, parties
                _ = s.execute(
                    update(Transaction)
                    .where(
                        tuple_(Transaction.occurred_at, Transaction.id).in_([
                            (occurred_at, row_id) for occurred_at, row_id, _ in keys
                        ]),
                        Transaction.status == TransactionStatus.BACKFILL,
                    )
                    .values(status=TransactionStatus.RECEIVED)
                    .execution_options(synchronize_session=False)
                )
            released += len(keys)
            parties.update(row_party for _, _, row_party in keys)
            last_at, last_id, _ = keys[-1]
            after = (last_at, last_id)

    def backfill(
        self,
        tenant_id: UUID,
        payload: dict[str, JsonValue],
        *,
        job: Job | None,
        actor: str,
    ) -> BackfillReport:
        """Release the requested rows and drain each of their parties in turn.

        ``job`` is the claimed job whose lease the drains extend; the CLI, which
        runs without a lease, passes None and an ``actor``.

        Returns:
            The rows released, the parties drained and the rows processed.

        """
        first = date.fromisoformat(str(payload["from"]))
        last = date.fromisoformat(str(payload["to"]))
        party = payload.get("party_id")
        party_id = None if party is None else UUID(str(party))
        ctx = TenantContext.system(tenant_id, actor)
        released, parties = self._release(
            ctx,
            start=_midnight(first),
            end=_midnight(last + timedelta(days=1)),
            party_id=party_id,
        )
        processed = 0
        for each in sorted(parties):
            report = process_party(
                self._session_factory,
                tenant_id,
                each,
                queue=self._queue,
                job=job,
                settings=self._settings,
                actor=actor,
            )
            processed += report.processed
        return BackfillReport(
            released=released, parties=len(parties), processed=processed
        )

    def run(self, _session: Session, job: Job) -> None:
        """Run the job's backfill; each page and row commits in its own session."""
        _ = self.backfill(job.tenant_id, job.payload, job=job, actor=f"job:{job.id}")
