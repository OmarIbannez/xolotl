"""The processing counters of ``GET /v1/monitoring/status`` (SP-04 spec §15, OPS-501).

Per tenant: ``RECEIVED`` rows by age since receipt, the ``PARKED`` and ``BACKFILL``
counts (by the transaction's status: an un-parked row keeps its
``transaction_parked`` entry), the oldest ``RECEIVED`` receipt, the running
``monitoring.process`` jobs, and for every registered partitioned table how many
consecutive months after the current UTC month already have their partition.
"""

import itertools
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import ColumnElement, and_, func, select, text
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.kinds import PROCESS_KIND
from xolotl.modules.monitoring.schemas import MonitoringStatus
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.platform.enums import JobStatus
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.partitions import next_month, partition_name, partitioned_tables

# Upper bounds of the age bands, in order; the last band has none.
AGE_BANDS: tuple[tuple[str, timedelta], ...] = (
    ("lt_1m", timedelta(minutes=1)),
    ("lt_10m", timedelta(minutes=10)),
    ("lt_1h", timedelta(hours=1)),
)
OLDEST_BAND = "gte_1h"
# Far enough to see every partition a maintenance run could have created.
_MONTHS_LOOKED_AT = 24
_CHILDREN_SQL = text("""
    SELECT c.relname FROM pg_inherits i
    JOIN pg_class c ON c.oid = i.inhrelid
    JOIN pg_class p ON p.oid = i.inhparent
    WHERE p.relname = :table
""")


def _received_by_age(session: Session, now: datetime) -> dict[str, int]:
    received = Transaction.received_at
    edges = [now - bound for _, bound in AGE_BANDS]
    bands: list[ColumnElement[bool]] = [received > edges[0]]
    bands.extend(
        and_(received <= newer, received > older)
        for newer, older in itertools.pairwise(edges)
    )
    bands.append(received <= edges[-1])
    row = session.execute(
        select(*(func.count().filter(band) for band in bands)).where(
            Transaction.status == TransactionStatus.RECEIVED
        )
    ).one()
    names = [name for name, _ in AGE_BANDS] + [OLDEST_BAND]
    return {name: int(count) for name, count in zip(names, row, strict=True)}


def _count(session: Session, status: TransactionStatus) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.status == status)
        )
        or 0
    )


def _months_ahead(session: Session, table: str, today: date) -> int:
    names = set(session.scalars(_CHILDREN_SQL, {"table": table}))
    month = next_month(today.replace(day=1))
    ahead = 0
    while ahead < _MONTHS_LOOKED_AT and partition_name(table, month) in names:
        ahead += 1
        month = next_month(month)
    return ahead


def monitoring_status(session: Session, *, now: datetime) -> MonitoringStatus:
    """Read the session's tenant's processing counters at ``now``.

    Returns:
        The counters.

    """
    oldest = session.scalar(
        select(func.min(Transaction.received_at)).where(
            Transaction.status == TransactionStatus.RECEIVED
        )
    )
    running = (
        session.scalar(
            select(func.count())
            .select_from(JobRow)
            .where(JobRow.kind == PROCESS_KIND, JobRow.status == JobStatus.RUNNING)
        )
        or 0
    )
    today = now.astimezone(UTC).date()
    return MonitoringStatus(
        received_by_age=_received_by_age(session, now),
        parked=_count(session, TransactionStatus.PARKED),
        backfill=_count(session, TransactionStatus.BACKFILL),
        oldest_received_at=oldest,
        running_process_jobs=running,
        partitions_ahead={
            spec.table: _months_ahead(session, spec.table, today)
            for spec in partitioned_tables()
        },
    )
