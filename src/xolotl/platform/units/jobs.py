"""The daily ``units.fetch`` job: fetch the missing UDI and FIX values and store them.

Each tenant has a ``units.fetch`` schedule (the job system is tenant-scoped); the
values are shared, and ``upsert_value`` inserts nothing for an unchanged value, so
the second tenant's run of the day costs one cheap call per unit. A source failure
notifies ADMIN and MLRO and completes the job: the next slot fetches again.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from xolotl.platform import clock
from xolotl.platform.calendar import MEXICO_CITY
from xolotl.platform.enums import Role
from xolotl.platform.events import Notifier
from xolotl.platform.jobs import Job, register_job_handler
from xolotl.platform.logging import safe_exception_summary
from xolotl.platform.units.errors import UnitSourceError
from xolotl.platform.units.sources import UnitValueSource
from xolotl.platform.units.store import current_row, latest_valid_from, upsert_value

logger = logging.getLogger(__name__)

UNITS_FETCH_KIND = "units.fetch"
FETCH_UNITS: tuple[str, ...] = ("UDI", "USD_MXN_FIX")
BACKFILL_DAYS = 31
FAILED_TEMPLATE = "units.fetch_failed"
_NOTIFIED = (Role.ADMIN, Role.MLRO)


@dataclass(frozen=True, slots=True)
class FetchReport:
    """What one fetch of one unit over one range did."""

    unit_code: str
    start: date
    end: date
    fetched: int
    inserted: int
    corrected: int


def fetch_missing(
    session: Session,
    source: UnitValueSource,
    unit_code: str,
    *,
    start: date,
    end: date,
    now: datetime,
) -> FetchReport:
    """Fetch a unit's values in ``[start, end]`` and upsert them.

    A value whose day has no current row is inserted; one that differs from the
    current row is a correction; an unchanged one is left alone.

    Returns:
        The counts of rows fetched, inserted and corrected.

    """
    values = source.fetch(unit_code, start, end)
    inserted = corrected = 0
    for value in values:
        existing = current_row(session, unit_code, value.valid_from)
        is_current = existing is not None and existing.valid_from == value.valid_from
        if upsert_value(session, value, fetched_at=now) is None:
            continue
        if is_current:
            corrected += 1
        else:
            inserted += 1
    return FetchReport(
        unit_code=unit_code,
        start=start,
        end=end,
        fetched=len(values),
        inserted=inserted,
        corrected=corrected,
    )


class UnitsFetchJob:
    """The ``units.fetch`` handler over a source, a notifier and a clock."""

    def __init__(
        self,
        source: UnitValueSource,
        *,
        notifier: Notifier,
        clock_fn: Callable[[], datetime] = clock.now,
    ) -> None:
        """Fetch from ``source``; tell ADMIN and MLRO through ``notifier``."""
        self._source: UnitValueSource = source
        self._notifier: Notifier = notifier
        self._clock_fn: Callable[[], datetime] = clock_fn

    def window(self, session: Session, unit_code: str) -> tuple[date, date] | None:
        """Work out the days a unit still lacks, up to today in Mexico City.

        Returns:
            ``(latest stored day + 1, today)``, or the last 31 days for a unit with
            no rows, or ``None`` when the store is up to date.

        """
        today = self._clock_fn().astimezone(ZoneInfo(MEXICO_CITY)).date()
        latest = latest_valid_from(session, unit_code)
        start = (
            today - timedelta(days=BACKFILL_DAYS)
            if latest is None
            else latest + timedelta(days=1)
        )
        return None if start > today else (start, today)

    def run(self, session: Session, _job: Job) -> None:
        """Fetch each unit of ``FETCH_UNITS``; a failing one is reported, not raised."""
        for unit_code in FETCH_UNITS:
            window = self.window(session, unit_code)
            if window is None:
                continue
            start, end = window
            try:
                _ = fetch_missing(
                    session,
                    self._source,
                    unit_code,
                    start=start,
                    end=end,
                    now=self._clock_fn(),
                )
            except UnitSourceError as error:
                for role in _NOTIFIED:
                    _ = self._notifier.notify(
                        session,
                        template=FAILED_TEMPLATE,
                        data={
                            "unit": unit_code,
                            "from": start.isoformat(),
                            "to": end.isoformat(),
                        },
                        recipient_role=role,
                    )
                logger.warning(
                    "units fetch failed",
                    extra={"unit": unit_code, "error": safe_exception_summary(error)},
                )


def register_units(
    *,
    source: UnitValueSource,
    notifier: Notifier,
    clock_fn: Callable[[], datetime] = clock.now,
) -> UnitsFetchJob:
    """Build the ``units.fetch`` job and register it as that kind's handler.

    Returns:
        The job, whose ``run`` is now the handler.

    """
    job = UnitsFetchJob(source, notifier=notifier, clock_fn=clock_fn)
    register_job_handler(UNITS_FETCH_KIND, job.run)
    return job
