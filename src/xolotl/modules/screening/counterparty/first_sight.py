"""First sight of a counterparty: its name index row and its screening job (§11.1).

``resolve`` has no queue parameter and the spec keeps its signature, so this module
holds a process-level queue that composition installs (``set_first_sight_queue``,
like ``set_index_holder``; plan 4c Deviation 5). When ``resolve`` inserts a row it
calls ``on_counterparty_created``, which indexes the name and enqueues one
``screening.screen_counterparties`` job keyed by the UTC minute
(``screen_counterparties:<YYYY-MM-DDTHH:MM>``), so a batch naming a thousand new
counterparties enqueues one job, not a thousand. Without a queue (a unit test of the
resolver) the hook indexes the name and enqueues nothing.

The job screens one page of never-screened rows per transaction
(``counterparty.runs.handle_first_sight``). This module stays free of the screening
engine: monitoring reaches it through ``resolve``, and contract
``monitoring-boundaries`` forbids monitoring any path to the engine's models.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.names import index_counterparty_name
from xolotl.platform import clock
from xolotl.platform.db import bound_context
from xolotl.platform.jobs.queue import PostgresJobQueue

FIRST_SIGHT_KIND = "screening.screen_counterparties"


class _Slot:
    def __init__(self) -> None:
        self.queue: PostgresJobQueue | None = None


_slot = _Slot()


def set_first_sight_queue(queue: PostgresJobQueue | None) -> None:
    """Install the process's first-sight queue (composition, tests); None removes it."""
    _slot.queue = queue


def enqueue_first_sight(s: Session) -> UUID | None:
    """Enqueue this minute's first-sight job in the caller's transaction.

    Returns:
        The new job's id; ``None`` when this minute's job is already pending or
        running, or when no queue is installed.

    """
    if _slot.queue is None:
        return None
    return _slot.queue.enqueue(
        s,
        kind=FIRST_SIGHT_KIND,
        key=f"screen_counterparties:{clock.now():%Y-%m-%dT%H:%M}",
        payload={},
    )


def enqueue_follow_up(
    s: Session, job_id: UUID, *, run_after: datetime | None = None
) -> UUID | None:
    """Enqueue the page after a first-sight job's page.

    Returns:
        The new job's id, or ``None`` without a queue or for a duplicate.

    """
    if _slot.queue is None:
        return None
    return _slot.queue.enqueue(
        s,
        kind=FIRST_SIGHT_KIND,
        key=f"screen_counterparties:continue:{job_id}",
        payload={},
        run_after=run_after,
    )


def on_counterparty_created(s: Session, counterparty_id: UUID) -> None:
    """Index a new counterparty's name and enqueue its first-sight screen."""
    row = s.get_one(
        Counterparty, (bound_context(s).require_tenant_id(), counterparty_id)
    )
    _ = index_counterparty_name(s, row)
    _ = enqueue_first_sight(s)


def unscreened_page(s: Session, *, limit: int) -> list[Counterparty]:
    """Lock the oldest counterparties that have no subject yet.

    Rows another transaction holds are skipped (``SKIP LOCKED``), so two pages never
    screen one counterparty twice and a page never waits on an ingest.

    Returns:
        Up to ``limit`` rows, oldest first.

    """
    return list(
        s.scalars(
            select(Counterparty)
            .where(
                Counterparty.tenant_id == bound_context(s).require_tenant_id(),
                Counterparty.screening_subject_id.is_(None),
            )
            .order_by(Counterparty.first_seen_at, Counterparty.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


def any_unscreened(s: Session) -> bool:
    """Tell whether a counterparty without a subject is left, locked or not.

    Returns:
        True when one is.

    """
    return bool(
        s.scalar(
            select(
                exists().where(
                    Counterparty.tenant_id == bound_context(s).require_tenant_id(),
                    Counterparty.screening_subject_id.is_(None),
                )
            )
        )
    )
