"""The daily ``monitoring.partitions`` job: every registered table kept ahead.

Partitions are created through the admin engine (the app role owns no table), for
every table registered with ``xolotl.platform.partitions`` (``audit_event``,
``transaction``, and plan 4c's ``txn_decision`` once it registers), from this
month to ``monitoring_partition_months_ahead`` months ahead. Each tenant's schedule
runs it; the calls take turns on the helper's per-table lock and find the months
already there, so a second run creates nothing.
"""

import logging

from sqlalchemy.orm import Session

from xolotl.platform.db import get_admin_engine
from xolotl.platform.jobs.queue import Job
from xolotl.platform.partitions import PartitionReport, ensure_all_partitions

logger = logging.getLogger(__name__)


def maintain_partitions(*, months_ahead: int) -> dict[str, PartitionReport]:
    """Create the missing months of every registered table, in one transaction.

    Returns:
        Each table's report, in registration order.

    """
    with get_admin_engine().begin() as conn:
        return ensure_all_partitions(conn, months_ahead=months_ahead)


def describe(table: str, report: PartitionReport) -> str:
    """Say what one table's run created and skipped, in one line.

    Returns:
        ``<table>: created <names|none>; skipped <name (sqlstate)|none>``.

    """
    created = ", ".join(report.created) or "none"
    skipped = (
        ", ".join(f"{name} ({state})" for name, state in report.skipped.items())
        or "none"
    )
    return f"{table}: created {created}; skipped {skipped}"


class PartitionsJob:
    """The handler of ``monitoring.partitions``."""

    def __init__(self, *, months_ahead: int) -> None:
        self._months_ahead: int = months_ahead

    def run(self, _session: Session, _job: Job) -> None:
        """Keep every registered table's months ahead and log each table's result."""
        reports = maintain_partitions(months_ahead=self._months_ahead)
        for table, report in reports.items():
            logger.info(
                "partitions ensured",
                extra={
                    "table": table,
                    "partitions_created": report.created,
                    "partitions_skipped": sorted(report.skipped),
                },
            )
