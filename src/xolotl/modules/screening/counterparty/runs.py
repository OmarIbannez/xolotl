"""Screening counterparties: one subject, one run per entity type (SP-04 spec §11.2).

``screen_counterparty`` screens a counterparty's subject once per entity type of
``entity_types_for`` (plan 4c Deviation 2), each through ``screen()`` with the
``entity_type`` option, then stamps ``screened_at``. The subject's status reaches the
row through ``refresh_subject_status`` (Deviation 4).

``handle_first_sight`` is the ``screening.screen_counterparties`` job: one page of
``MONITORING_COUNTERPARTY_SCREEN_BATCH`` never-screened counterparties in one
transaction (``mode=COUNTERPARTY``, ``trigger_type=JOB``, ref the job id). A full
page enqueues the next one (key ``screen_counterparties:continue:<job id>``). A short
page that leaves a counterparty behind enqueues it after ``SKIPPED_RETRY``: the row
was locked by an ingest, or committed while the page ran under a minute key this job
already held, and would otherwise wait for the next new counterparty.

A page holds the row locks of up to ``MONITORING_COUNTERPARTY_SCREEN_BATCH``
counterparties until it commits, so an ingest that sights one of them again waits
for the page; accepted for the MVP.
"""

from datetime import timedelta
from typing import Unpack

from sqlalchemy.orm import Session

from xolotl.modules.screening.counterparty.first_sight import (
    any_unscreened,
    enqueue_follow_up,
    unscreened_page,
)
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.queries import entity_types_for
from xolotl.modules.screening.counterparty.status import mark_screened
from xolotl.modules.screening.counterparty.subjects import subject_for_counterparty
from xolotl.modules.screening.engine.enums import RunMode, TriggerType
from xolotl.modules.screening.engine.screen import (
    RunResult,
    ScreenOptions,
    Trigger,
    screen,
)
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.jobs.queue import Job

SKIPPED_RETRY = timedelta(seconds=30)


def screen_counterparty(
    s: Session,
    row: Counterparty,
    *,
    mode: RunMode,
    trigger: Trigger,
    **options: Unpack[ScreenOptions],
) -> list[RunResult]:
    """Screen a counterparty under each of its entity types and stamp ``screened_at``.

    Returns:
        One completed run per entity type, in ``entity_types_for`` order.

    """
    subject = subject_for_counterparty(s, row.id, for_update=True)
    results: list[RunResult] = []
    for entity_type in entity_types_for(row):
        run_options = options.copy()
        run_options["entity_type"] = entity_type
        results.append(screen(s, subject, mode=mode, trigger=trigger, **run_options))
    mark_screened(s, row.id)
    return results


def screen_unscreened(s: Session, *, trigger: Trigger, limit: int) -> int:
    """Screen one page of counterparties that have never been screened.

    Returns:
        How many counterparties the page screened.

    """
    page = unscreened_page(s, limit=limit)
    for row in page:
        _ = screen_counterparty(s, row, mode=RunMode.COUNTERPARTY, trigger=trigger)
    return len(page)


def handle_first_sight(s: Session, job: Job) -> None:
    """Screen one page of new counterparties and enqueue the next page if any.

    The page is one transaction, so one counterparty whose screen fails rolls the
    whole page back, and first sight stalls on it until the job dead-letters; the
    ``screening screen-counterparties`` command and C2's walk get around it.
    """
    limit = get_settings().monitoring_counterparty_screen_batch
    screened = screen_unscreened(
        s, trigger=Trigger(TriggerType.JOB, str(job.id)), limit=limit
    )
    if screened == limit:
        _ = enqueue_follow_up(s, job.id)
    elif any_unscreened(s):
        _ = enqueue_follow_up(s, job.id, run_after=clock.now() + SKIPPED_RETRY)
