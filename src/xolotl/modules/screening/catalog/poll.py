"""The per-tenant ``lists.poll`` job (SP-03 spec §6.2 to §6.4).

Every tenant runs it on ``XOLOTL_LISTS_POLL_CRON``. Its tenant half, in the job's
session: reconcile the subscriptions with the effective policy, catch the tenant up
on new shared versions, then notify stale sources and a missing LPB.
"""

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from xolotl.modules.screening.catalog.subscriptions import (
    catch_up,
    check_staleness,
    reconcile_subscriptions,
    remind_lpb_missing,
)
from xolotl.modules.screening.events import PRIVATE_LIST_VERSION_ACTIVATED
from xolotl.platform import clock
from xolotl.platform.calendar import MEXICO_CITY
from xolotl.platform.db import SessionFactory
from xolotl.platform.events import Notifier, register_staff_only
from xolotl.platform.jobs import Job, register_job_handler

LISTS_POLL_KIND = "lists.poll"


class ListsPollJob:
    """The ``lists.poll`` handler over a session factory, a notifier and a clock."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        notifier: Notifier,
        clock_fn: Callable[[], datetime] = clock.now,
    ) -> None:
        """Notify through ``notifier``; the shared half opens sessions of its own."""
        self._session_factory: SessionFactory = session_factory
        self._notifier: Notifier = notifier
        self._clock_fn: Callable[[], datetime] = clock_fn

    def run(self, session: Session, _job: Job) -> None:
        """Run the tenant half of the poll in the job's session."""
        now = self._clock_fn()
        today = now.astimezone(ZoneInfo(MEXICO_CITY)).date()
        _ = reconcile_subscriptions(session, today=today)
        _ = catch_up(session)
        _ = check_staleness(session, self._notifier, now=now)
        _ = remind_lpb_missing(session, self._notifier, now=now)


def register_lists(
    *, session_factory: SessionFactory, notifier: Notifier
) -> ListsPollJob:
    """Build the ``lists.poll`` job and register it as that kind's handler.

    Also marks ``private_list.version_activated.v1`` staff-only, so no webhook ever
    carries a private-list version (marking it again is harmless).

    Returns:
        The job, whose ``run`` is now the handler.

    """
    job = ListsPollJob(session_factory=session_factory, notifier=notifier)
    register_job_handler(LISTS_POLL_KIND, job.run)
    register_staff_only(PRIVATE_LIST_VERSION_ACTIVATED)
    return job
