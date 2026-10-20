"""Re-screen batch bookkeeping: open by ``(kind, ref)``, advance per page (spec §10).

A batch is opened with ``INSERT ... ON CONFLICT DO NOTHING`` on its unique
``(tenant_id, kind, ref)`` and then read under a row lock, so a redelivered catalog
event or a second schedule tick finds the row already there. A ``COMPLETED`` batch is
never reopened (a second periodic job in the same month does nothing); a ``FAILED``
one resumes from its cursor.

Each page job advances its batch in the page's own transaction. The cursor is where
the next page starts: an offset into the version's delta rows for ``LIST_DELTA``, the
last party id walked for the other kinds. It never moves backwards, so a replayed
page sees that the batch has passed it and is skipped.

The job runner has no hook for a job that runs out of attempts, so each page
handler is wrapped in ``page_job``: when the last attempt fails, the wrapper rolls
the page back, marks the batch ``FAILED`` with a code-only ``last_error`` in a
transaction of its own, and re-raises so the runner dead-letters the job as usual.
The next open of the same ``(kind, ref)`` resumes the batch from its cursor.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.screening.engine.enums import BatchKind, BatchStatus
from xolotl.modules.screening.engine.models import ScreeningBatch
from xolotl.platform import clock
from xolotl.platform.db import bound_context
from xolotl.platform.errors import NotFoundError
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.queue import Job, JobHandler

# The first page of a walker starts before every party id.
WALKER_START = "start"


def open_batch(
    s: Session, *, kind: BatchKind, ref: str, total: int | None
) -> ScreeningBatch | None:
    """Open the tenant's batch of ``(kind, ref)``, or find the one already open.

    ``total`` is kept from the first open; a resumed batch keeps its counts. A resumed
    (previously ``FAILED``) batch's next page starts at ``batch.cursor``, so callers
    must enqueue from the cursor, not from page one.

    Returns:
        The locked batch, ``RUNNING``; None when it is already ``COMPLETED``.

    """
    _ = s.execute(
        insert(ScreeningBatch)
        .values(
            tenant_id=bound_context(s).require_tenant_id(),
            id=uuid7(),
            kind=kind,
            ref=ref,
            status=BatchStatus.RUNNING,
            total=total,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "kind", "ref"])
    )
    batch = s.scalars(
        select(ScreeningBatch)
        .where(ScreeningBatch.kind == kind, ScreeningBatch.ref == ref)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if batch.status is BatchStatus.COMPLETED:
        return None
    if batch.status is BatchStatus.FAILED:
        batch.status = BatchStatus.RUNNING
        batch.last_error = None
    return batch


def lock_batch(s: Session, batch_id: UUID) -> ScreeningBatch:
    """Read the tenant's batch under a row lock, for a page to advance it.

    Returns:
        The batch.

    Raises:
        NotFoundError: When the tenant has no such batch.

    """
    batch = s.scalars(
        select(ScreeningBatch)
        .where(ScreeningBatch.id == batch_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if batch is None:
        msg = f"no screening batch {batch_id}"
        raise NotFoundError(msg)
    return batch


def _position(kind: BatchKind, cursor: str) -> int:
    # Offsets compare as numbers and party ids as UUIDs ("1000" follows "200"); a
    # walker's first page sorts before every party id.
    if kind is BatchKind.LIST_DELTA:
        return int(cursor)
    if cursor == WALKER_START:
        return -1
    return UUID(cursor).int


def advance_batch(
    batch: ScreeningBatch, *, done: int, runs: int, new_matches: int, cursor: str
) -> None:
    """Add a page's counts to the batch and move its cursor to the next page."""
    batch.done += done
    batch.runs += runs
    batch.new_matches += new_matches
    if batch.cursor is None or _position(batch.kind, cursor) > _position(
        batch.kind, batch.cursor
    ):
        batch.cursor = cursor


def complete_batch(batch: ScreeningBatch, *, note: str | None = None) -> None:
    """Mark the batch ``COMPLETED`` now, with an optional code-only note."""
    batch.status = BatchStatus.COMPLETED
    batch.finished_at = clock.now()
    batch.last_error = note


def page_already_done(batch: ScreeningBatch, cursor: str) -> bool:
    """Tell whether the page starting at ``cursor`` has already been committed.

    Returns:
        True when the batch is ``COMPLETED`` or its cursor has passed the page's.

    """
    if batch.status is BatchStatus.COMPLETED:
        return True
    if batch.cursor is None:
        return False
    return _position(batch.kind, batch.cursor) > _position(batch.kind, cursor)


def fail_batch(batch: ScreeningBatch, error: str) -> None:
    """Mark the batch ``FAILED`` with a code-only reason; its cursor stays."""
    batch.status = BatchStatus.FAILED
    batch.last_error = error


def page_job(handler: JobHandler) -> JobHandler:
    """Wrap a page handler so its batch fails when the job's last attempt does.

    The page handler's payload carries ``batch_id``. An attempt that leaves retries
    raises as it is. On the last one, the page's work is rolled back, the batch is
    marked ``FAILED`` (``JOB_FAILED:<exception class>``, no message, which could
    carry row values) and committed, and the error is raised again for the runner
    to record on the job.

    Returns:
        The wrapped handler.

    """

    def run(s: Session, job: Job) -> None:
        try:
            handler(s, job)
        except Exception as error:
            if job.attempts < job.max_attempts:
                raise
            s.rollback()
            batch = lock_batch(s, UUID(str(job.payload["batch_id"])))
            fail_batch(batch, f"JOB_FAILED:{type(error).__name__}")
            s.commit()
            raise

    return run
