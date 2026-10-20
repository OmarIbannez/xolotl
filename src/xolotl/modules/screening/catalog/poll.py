"""The per-tenant ``lists.poll`` job (SP-03 spec §6.2 to §6.4).

Every tenant runs it on ``XOLOTL_LISTS_POLL_CRON``. Its shared half, when network
fetching is on: claim at most one due source under a lease in a short session of its
own, fetch it outside any transaction, ingest it in another short session, and
record the outcome; a failure backs the source off and keeps its active version. The
lease makes one fetch per interval across all tenants, and a lease left by a job
that died frees itself after ``LEASE``. Its tenant half, in the job's session:
reconcile the subscriptions with the effective policy, catch the tenant up on new
shared versions (one ingested this run included), then notify stale sources and a
missing LPB.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, or_, select, update
from sqlalchemy.orm import Session

from xolotl.modules.screening.catalog.fetcher import (
    FetchResult,
    ListFetcher,
    ListFetchError,
)
from xolotl.modules.screening.catalog.ingest import (
    IngestReport,
    ingest_file,
    record_success,
)
from xolotl.modules.screening.catalog.models import ListSource
from xolotl.modules.screening.catalog.subscriptions import (
    catch_up,
    check_staleness,
    reconcile_subscriptions,
    remind_lpb_missing,
)
from xolotl.modules.screening.errors import ListMassRemovalError
from xolotl.modules.screening.lists import ListFormatError
from xolotl.platform import clock
from xolotl.platform.calendar import MEXICO_CITY
from xolotl.platform.db import SessionFactory, bound_context, tenant_session
from xolotl.platform.events import Notifier
from xolotl.platform.jobs import Job, register_job_handler
from xolotl.platform.storage import ObjectStore
from xolotl.platform.tenancy.context import TenantContext

LISTS_POLL_KIND = "lists.poll"
LEASE = timedelta(minutes=10)
MAX_BACKOFF = timedelta(hours=6)
# The longest last_error kept: a code and a short reason, never a body.
MAX_ERROR_LENGTH = 200
# Past this many failures in a row every interval has reached MAX_BACKOFF; the cap
# keeps the multiplication inside timedelta's range.
_MAX_DOUBLINGS = 16

logger = logging.getLogger(__name__)

PollResult = Literal["NOT_MODIFIED", "SAME_FILE", "INGESTED", "FAILED"]


@dataclass(frozen=True, slots=True)
class PollOutcome:
    """What one fetch of a source came to; ``error`` is set only when it FAILED."""

    code: str
    result: PollResult
    report: IngestReport | None
    error: str | None


def backoff(poll_interval: timedelta, failures: int) -> timedelta:
    """Return how long a source waits after ``failures`` failed attempts in a row.

    Returns:
        ``min(poll_interval * 2**failures, MAX_BACKOFF)``.

    """
    return min(poll_interval * (1 << min(failures, _MAX_DOUBLINGS)), MAX_BACKOFF)


def _lease_free(now: datetime) -> ColumnElement[bool]:
    return or_(ListSource.lease_until.is_(None), ListSource.lease_until < now)


def _take_lease(
    session: Session, code: ColumnElement[str], *, holder: str, now: datetime
) -> ListSource | None:
    return session.scalars(
        update(ListSource)
        .where(ListSource.code == code)
        .values(lease_until=now + LEASE, lease_holder=holder)
        .returning(ListSource)
        .execution_options(populate_existing=True)
    ).one_or_none()


def claim_due_source(
    session: Session, *, holder: str, now: datetime
) -> ListSource | None:
    """Lease the enabled source that has waited longest for its poll, if any.

    One statement (spec §6.2 step 1): the due, unleased source with the earliest
    ``next_poll_at`` is locked with ``SKIP LOCKED`` and leased to ``holder`` until
    ``now + LEASE``, so concurrent polls never claim the same source. The caller
    commits before fetching.

    Returns:
        The leased source, or None when no source is due and free.

    """
    due = (
        select(ListSource.code)
        .where(ListSource.enabled, ListSource.next_poll_at <= now, _lease_free(now))
        .order_by(ListSource.next_poll_at)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    return _take_lease(session, due, holder=holder, now=now)


def claim_source(
    session: Session, code: str, *, holder: str, now: datetime
) -> ListSource | None:
    """Lease one enabled source whether or not it is due (``xolotl lists fetch``).

    Returns:
        The leased source, or None when it is unknown, disabled or leased to
        someone else.

    """
    free = (
        select(ListSource.code)
        .where(ListSource.code == code, ListSource.enabled, _lease_free(now))
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    return _take_lease(session, free, holder=holder, now=now)


def _release(source: ListSource) -> None:
    source.lease_until = None
    source.lease_holder = None


def _error_text(exc: Exception) -> str:
    # A code and a short reason: the fetcher's message, the library's or the
    # guard's code and detail, or only the type of anything unexpected.
    if isinstance(exc, ListFetchError):
        text = str(exc)
    elif isinstance(exc, (ListFormatError, ListMassRemovalError)):
        text = f"{exc.code}: {exc.detail}"
    else:
        text = f"INTERNAL: {type(exc).__name__}"
    return text[:MAX_ERROR_LENGTH]


def _store_result(
    session: Session,
    code: str,
    result: FetchResult,
    *,
    holder: str,
    store: ObjectStore,
    now: datetime,
) -> PollOutcome:
    if result.not_modified or result.body is None:
        source = session.get_one(ListSource, code)
        record_success(session, source, now=now, changed=False)
        _release(source)
        return PollOutcome(code=code, result="NOT_MODIFIED", report=None, error=None)
    report = ingest_file(
        session, code, result.body, store=store, loaded_by=holder, fetched_at=now
    )
    source = session.get_one(ListSource, code)
    source.etag = result.etag
    source.last_modified = result.last_modified
    _release(source)
    return PollOutcome(
        code=code,
        result="SAME_FILE" if report.same_file else "INGESTED",
        report=report,
        error=None,
    )


def poll_source(
    session_factory: SessionFactory,
    source_code: str,
    *,
    holder: str,
    fetcher: ListFetcher,
    store: ObjectStore,
    now: datetime,
) -> PollOutcome:
    """Fetch one source and ingest what changed, then release its lease.

    The fetch runs outside any session; the outcome is written in a short session
    of its own. A 304 refreshes the source's freshness and keeps its validators; a
    body is ingested (a file equal to the active version's makes no version) and
    its validators stored. Any failure (a fetch error, a malformed file, the
    mass-removal guard, or anything unexpected, which is also logged) leaves the
    active version as it was and records ``last_error``, one more failure and the
    backed-off ``next_poll_at`` in a fresh session.

    Returns:
        What the poll came to.

    """
    ctx = TenantContext.platform(holder)
    try:
        with tenant_session(ctx, factory=session_factory) as s:
            source = s.get_one(ListSource, source_code)
            url, etag, last_modified = source.url, source.etag, source.last_modified
        result = fetcher.fetch(url, etag=etag, last_modified=last_modified)
        with tenant_session(ctx, factory=session_factory) as s:
            return _store_result(
                s, source_code, result, holder=holder, store=store, now=now
            )
    except (ListFetchError, ListFormatError, ListMassRemovalError) as exc:
        error = _error_text(exc)
    except Exception as exc:
        # Anything unexpected still releases the lease and backs the source off.
        logger.exception("lists poll: %s failed unexpectedly", source_code)
        error = _error_text(exc)
    with tenant_session(ctx, factory=session_factory) as s:
        source = s.get_one(ListSource, source_code)
        source.last_attempt_at = now
        source.last_error = error
        source.consecutive_failures += 1
        source.next_poll_at = now + backoff(
            source.poll_interval, source.consecutive_failures
        )
        _release(source)
    return PollOutcome(code=source_code, result="FAILED", report=None, error=error)


class ListsPollJob:
    """The ``lists.poll`` handler: the shared fetch, then the tenant half."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        notifier: Notifier,
        fetcher: ListFetcher,
        store: ObjectStore,
        fetching: bool,
        clock_fn: Callable[[], datetime] = clock.now,
    ) -> None:
        """Fetch through ``fetcher`` into ``store`` only when ``fetching`` is on."""
        self._session_factory: SessionFactory = session_factory
        self._notifier: Notifier = notifier
        self._fetcher: ListFetcher = fetcher
        self._store: ObjectStore = store
        self.fetching: bool = fetching
        self._clock_fn: Callable[[], datetime] = clock_fn

    def run(self, session: Session, _job: Job) -> None:
        """Poll at most one due shared source, then run the tenant half."""
        now = self._clock_fn()
        if self.fetching:
            self._poll_one(bound_context(session).actor_id, now)
        today = now.astimezone(ZoneInfo(MEXICO_CITY)).date()
        _ = reconcile_subscriptions(session, today=today)
        _ = catch_up(session)
        _ = check_staleness(session, self._notifier, now=now)
        _ = remind_lpb_missing(session, self._notifier, now=now)

    def _poll_one(self, holder: str, now: datetime) -> None:
        ctx = TenantContext.platform(holder)
        with tenant_session(ctx, factory=self._session_factory) as s:
            source = claim_due_source(s, holder=holder, now=now)
            code = None if source is None else source.code
        if code is None:
            return
        outcome = poll_source(
            self._session_factory,
            code,
            holder=holder,
            fetcher=self._fetcher,
            store=self._store,
            now=now,
        )
        failed = outcome.result == "FAILED"
        logger.log(
            logging.WARNING if failed else logging.INFO,
            "lists poll: %s %s%s",
            code,
            outcome.result,
            f" {outcome.error}" if failed else "",
        )


def register_lists(
    *,
    session_factory: SessionFactory,
    notifier: Notifier,
    fetcher: ListFetcher,
    store: ObjectStore,
    fetching: bool,
) -> ListsPollJob:
    """Build the ``lists.poll`` job and register it as that kind's handler.

    Returns:
        The job, whose ``run`` is now the handler.

    """
    job = ListsPollJob(
        session_factory=session_factory,
        notifier=notifier,
        fetcher=fetcher,
        store=store,
        fetching=fetching,
    )
    register_job_handler(LISTS_POLL_KIND, job.run)
    return job
