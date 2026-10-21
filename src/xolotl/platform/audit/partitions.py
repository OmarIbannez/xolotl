"""Monthly partitions of ``audit_event``: wrappers over ``xolotl.platform.partitions``.

Importing this module registers ``audit_event`` with its append-only TRUNCATE
guard, so ``migrate.upgrade()`` and the partition job keep it ahead.
"""

from datetime import date

from sqlalchemy import Connection

from xolotl.platform import partitions
from xolotl.platform.partitions import (
    LOCK_TIMEOUT,
    PartitionedTable,
    PartitionReport,
    next_month,
    register_partitioned_table,
)

__all__ = [
    "IMMUTABLE_FUNCTION",
    "LOCK_TIMEOUT",
    "TABLE",
    "PartitionReport",
    "ensure_partitions",
    "harden_partition",
    "next_month",
    "partition_name",
]

TABLE = "audit_event"
IMMUTABLE_FUNCTION = "audit_event_immutable"

register_partitioned_table(PartitionedTable(TABLE, "at", IMMUTABLE_FUNCTION))


def partition_name(month: date) -> str:
    return partitions.partition_name(TABLE, month)


def harden_partition(conn: Connection, name: str) -> None:
    """Force RLS on an ``audit_event`` partition and reject TRUNCATE on it."""
    partitions.harden_partition(conn, name, truncate_guard=IMMUTABLE_FUNCTION)


def ensure_partitions(
    conn: Connection, *, months_ahead: int = 3, start: date | None = None
) -> PartitionReport:
    """Create ``audit_event``'s partitions (``partitions.ensure_partitions``).

    Returns:
        The partitions created in this call and the ones skipped, by SQLSTATE.

    """
    return partitions.ensure_partitions(
        conn, table=TABLE, months_ahead=months_ahead, start=start
    )
