"""Monthly range partitions with forced RLS, for every partitioned table.

A partitioned table registers a ``PartitionedTable`` at import (``audit_event`` in
``platform.audit.partitions``; the SP-04 tables in their model modules), and
``migrate.upgrade()`` keeps every registered table's partitions ahead through
``ensure_all_partitions``. Each table has a ``<table>_default`` partition created by
its migration and one partition per month, ``<table>_yYYYYmMM``.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import cast

from sqlalchemy import Connection, exists, select, text
from sqlalchemy import table as table_
from sqlalchemy.exc import DBAPIError

from xolotl.platform import clock
from xolotl.platform.tenancy.ddl import rls_statements

logger = logging.getLogger(__name__)

# Creating a partition takes ACCESS EXCLUSIVE on its parent, and while that request
# waits every read and write of the parent queues behind it, so it gives up after
# this long.
LOCK_TIMEOUT = "5s"
# A retirement takes its locks with NOWAIT; anything it still waits for (a lock
# the lock set did not foresee) gives up after this.
RETIRE_LOCK_TIMEOUT = "1s"
_DECEMBER = 12
_MONTH_SUFFIX = re.compile(r"_y\d{4}m\d{2}")
# Table, column and guard names are interpolated into DDL, so they are restricted
# to plain lower-case identifiers when registered.
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")
_TURN_SQL = text("SELECT pg_advisory_xact_lock(hashtext(:key)::bigint)")
_LOCK_TIMEOUT_SQL = text("SELECT current_setting('lock_timeout')")
_SET_LOCK_TIMEOUT_SQL = text("SELECT set_config('lock_timeout', :value, true)")
# A month whose partition fails with one of these SQLSTATEs is logged and skipped,
# with the log level and the reason to give (formatted with the table); any other
# error propagates.
_SKIPPABLE: dict[str, tuple[int, str]] = {
    "23514": (
        logging.ERROR,
        "rows for that month sit in {table}_default; move them out first",
    ),
    "42P07": (logging.WARNING, "a table of that name already exists"),
    "55P03": (
        logging.WARNING,
        f"{{table}} stayed locked over {LOCK_TIMEOUT}; a later upgrade retries",
    ),
}


@dataclass(frozen=True, slots=True)
class PartitionedTable:
    """A table partitioned monthly by ``RANGE (column)``.

    ``truncate_guard`` names a trigger function run ``BEFORE TRUNCATE`` on each
    partition (``audit_event``'s append-only function); ``None`` adds no trigger.
    """

    table: str
    column: str
    truncate_guard: str | None = None


_REGISTRY: dict[str, PartitionedTable] = {}


def register_partitioned_table(spec: PartitionedTable) -> None:
    """Add ``spec`` to the registry; registering an identical spec again is a no-op.

    Raises:
        ValueError: When a name is not a plain lower-case identifier, or the table
            is already registered with a different column or guard.

    """
    for name in (spec.table, spec.column, spec.truncate_guard):
        if name is not None and not _IDENTIFIER.fullmatch(name):
            msg = f"{name!r} is not a plain lower-case identifier"
            raise ValueError(msg)
    existing = _REGISTRY.setdefault(spec.table, spec)
    if existing != spec:
        msg = f"{spec.table} is already registered as {existing}"
        raise ValueError(msg)


def partitioned_tables() -> tuple[PartitionedTable, ...]:
    """Every registered table.

    Returns:
        The registered specs, in registration order.

    """
    return tuple(_REGISTRY.values())


def partition_name(table: str, month: date) -> str:
    return f"{table}_y{month:%Y}m{month:%m}"


def is_partition_name(name: str) -> bool:
    """Whether ``name`` is the default or a monthly partition of a registered table.

    Returns:
        ``True`` for ``<table>_default`` and ``<table>_yYYYYmMM``.

    """
    for table in _REGISTRY:
        if name == f"{table}_default":
            return True
        if name.startswith(table) and _MONTH_SUFFIX.fullmatch(name[len(table) :]):
            return True
    return False


def next_month(d: date) -> date:
    return date(d.year + (d.month == _DECEMBER), d.month % _DECEMBER + 1, 1)


def harden_partition(
    conn: Connection, name: str, *, truncate_guard: str | None = None
) -> None:
    """Force RLS on a partition and, with a guard, reject TRUNCATE on it.

    Partitions get no GRANT: the application role reaches rows only through the
    parent table, and the parent's row triggers are cloned onto every partition.
    """
    for statement in rls_statements(name, grant=None):
        _ = conn.execute(text(statement))
    if truncate_guard is not None:
        _ = conn.execute(
            text(
                f"""
                CREATE TRIGGER {name}_no_truncate BEFORE TRUNCATE ON {name}
                FOR EACH STATEMENT EXECUTE FUNCTION {truncate_guard}()
                """
            )
        )


@dataclass(frozen=True, slots=True)
class PartitionReport:
    """What one ``ensure_partitions`` call did.

    ``created`` lists the partitions it created; ``skipped`` maps each partition it
    could not create yet to the SQLSTATE that stopped it.
    """

    created: list[str]
    skipped: dict[str, str]


def ensure_partitions(
    conn: Connection, *, table: str, months_ahead: int = 3, start: date | None = None
) -> PartitionReport:
    """Create ``table``'s partition for ``start``'s month and ``months_ahead`` more.

    Bounds are UTC midnights (explicit ``+00``) whatever the session ``TimeZone``, so
    partitions created from different sessions always abut. A registered table's
    ``truncate_guard`` is attached to each new partition.

    Callers take turns on a transaction-scoped advisory lock of the table's own
    (``hashtext('<table>_partitions')``), so two processes that start together, or
    a start and the daily job, never both try to create the same partition, and
    two tables never wait for each other. Each month is created and hardened in its
    own savepoint, with ``lock_timeout`` at ``LOCK_TIMEOUT``. A month that cannot
    be created yet is logged and skipped, and the next month is tried: rows for it
    already sit in ``<table>_default`` (23514), a table of that name exists
    (42P07), or another transaction held ``table`` for too long (55P03). Any other
    error propagates. The caller's ``lock_timeout`` is left as it was.

    Returns:
        The partitions created in this call and the ones skipped, by SQLSTATE.

    Raises:
        ValueError: When ``table`` is not registered, so its guard is unknown.

    """
    spec = _REGISTRY.get(table)
    if spec is None:
        msg = f"{table} is not a registered partitioned table"
        raise ValueError(msg)
    _ = conn.execute(_TURN_SQL, {"key": f"{table}_partitions"})
    callers_timeout = cast("str", conn.scalar(_LOCK_TIMEOUT_SQL))
    created: list[str] = []
    skipped: dict[str, str] = {}
    month = (start or clock.now().date()).replace(day=1)
    for _ in range(months_ahead + 1):
        name = partition_name(table, month)
        exists = cast(
            "int | None",
            conn.scalar(
                text("SELECT 1 FROM pg_class WHERE relname = :name"), {"name": name}
            ),
        )
        if exists is None:
            skipped_by = _create_partition(
                conn,
                table,
                month,
                callers_timeout=callers_timeout,
                guard=spec.truncate_guard,
            )
            if skipped_by is None:
                created.append(name)
            else:
                skipped[name] = skipped_by
        month = next_month(month)
    return PartitionReport(created=created, skipped=skipped)


def ensure_all_partitions(
    conn: Connection, *, months_ahead: int = 3
) -> dict[str, PartitionReport]:
    """Run ``ensure_partitions`` from this month for every registered table.

    Returns:
        Each table's report, in registration order.

    """
    return {
        spec.table: ensure_partitions(conn, table=spec.table, months_ahead=months_ahead)
        for spec in partitioned_tables()
    }


def _create_partition(
    conn: Connection,
    table: str,
    month: date,
    *,
    callers_timeout: str,
    guard: str | None,
) -> str | None:
    """Create and harden ``table``'s partition for ``month`` in a savepoint.

    Returns:
        ``None`` once the partition exists, or the SQLSTATE that skipped it.

    Raises:
        DBAPIError: When creating it fails for any reason not in ``_SKIPPABLE``.

    """
    name = partition_name(table, month)
    upper = next_month(month)
    try:
        with conn.begin_nested():
            _ = conn.execute(_SET_LOCK_TIMEOUT_SQL, {"value": LOCK_TIMEOUT})
            _ = conn.execute(
                text(
                    f"""
                    CREATE TABLE {name} PARTITION OF {table}
                    FOR VALUES FROM ('{month:%Y-%m-%d} 00:00:00+00')
                    TO ('{upper:%Y-%m-%d} 00:00:00+00')
                    """
                )
            )
            harden_partition(conn, name, truncate_guard=guard)
            # A setting made in a savepoint outlives its release: restore the caller's.
            _ = conn.execute(_SET_LOCK_TIMEOUT_SQL, {"value": callers_timeout})
    except DBAPIError as exc:
        sqlstate = cast("str | None", getattr(exc.orig, "sqlstate", None)) or ""
        if sqlstate not in _SKIPPABLE:
            raise
        level, reason = _SKIPPABLE[sqlstate]
        logger.log(
            level,
            "%s partition %s not created (sqlstate %s): %s",
            table,
            name,
            sqlstate,
            reason.format(table=table),
            extra={
                "table": table,
                "partition": name,
                "month": f"{month:%Y-%m}",
                "sqlstate": sqlstate,
            },
        )
        return sqlstate
    return None


# Every table a retirement locks besides the parent: the tables whose foreign keys
# reference it (DETACH checks them) and the tables it references (DROP removes the
# partition's foreign keys and their triggers from them).
_NEIGHBOURS_SQL = text(
    """
    SELECT DISTINCT CAST(other.oid AS regclass)::text AS name
    FROM pg_constraint k
    JOIN pg_class other ON other.oid = CASE
      WHEN k.conrelid = CAST(:table AS regclass) THEN k.confrelid
      ELSE k.conrelid END
    WHERE k.contype = 'f' AND k.conparentid = 0
      AND (k.conrelid = CAST(:table AS regclass)
           OR k.confrelid = CAST(:table AS regclass))
      AND other.oid <> CAST(:table AS regclass)
    ORDER BY name
    """
)


def retirement_lock_set(conn: Connection, table: str) -> list[str]:
    """Name every table retiring a partition of ``table`` locks, ``table`` first.

    Returns:
        ``table``, then its referencing and referenced tables by name.

    """
    names = cast("list[str]", conn.scalars(_NEIGHBOURS_SQL, {"table": table}).all())
    return [table, *names]


def retire_partition(conn: Connection, *, table: str, month: date) -> bool:
    """Detach and drop ``table``'s partition for ``month``, which must be empty.

    The caller deletes the month's rows first, every tenant's (the retention job,
    SP-04 spec §8.6); this refuses a partition that still holds any row, whoever's,
    so a partition is dropped only once nothing is left in it to lose. The default
    partition is never retired (only a ``<table>_yYYYYmMM`` name is built), nor a
    table with a truncate guard (``audit_event`` is append-only), nor this month or
    a later one.

    The call takes the table's partition lock (``hashtext('<table>_partitions')``,
    as ``ensure_partitions`` does), then ``ACCESS EXCLUSIVE`` with ``NOWAIT`` on
    the whole ``retirement_lock_set``: the parent, every table referencing it
    (``DETACH`` checks them) and every table it references (``DROP`` takes them,
    for ``transaction`` that includes ``party``). Taking them all at once and never
    queueing means a busy table fails the call at once (55P03) instead of holding
    up that table's readers; the caller retries on a later run. No row can arrive
    while it looks. The
    look is made with row security off on the partition: an owner that forced RLS
    binds would otherwise count no row and drop a full partition, and if RLS still
    applied the query would fail instead of reading zero. ``DETACH PARTITION
    CONCURRENTLY`` is not available: it refuses a table with a default partition,
    and every registered table has one. Everything runs in the caller's
    transaction, so a refusal or a failure leaves the partition attached; on
    success the caller's ``lock_timeout`` and ``row_security`` are restored.

    Returns:
        ``True`` once the partition is dropped, ``False`` when it does not exist.

    Raises:
        ValueError: When ``table`` is not registered or has a truncate guard, when
            ``month`` is not a past month, when a table of that name is not
            attached to ``table``, or when the partition still holds rows.

    """
    spec = _REGISTRY.get(table)
    if spec is None:
        msg = f"{table} is not a registered partitioned table"
        raise ValueError(msg)
    if spec.truncate_guard is not None:
        msg = f"{table} is append-only; its partitions are never retired"
        raise ValueError(msg)
    if month.replace(day=1) >= clock.now().date().replace(day=1):
        msg = f"only a past month's partition is retired, not {month:%Y-%m}"
        raise ValueError(msg)
    name = partition_name(table, month.replace(day=1))
    _ = conn.execute(_TURN_SQL, {"key": f"{table}_partitions"})
    present = cast(
        "int | None",
        conn.scalar(
            text("SELECT 1 FROM pg_class WHERE relname = :name"), {"name": name}
        ),
    )
    if present is None:
        return False
    attached = cast(
        "int | None",
        conn.scalar(
            text(
                """
                SELECT 1 FROM pg_inherits
                WHERE inhrelid = CAST(:name AS regclass)
                  AND inhparent = CAST(:table AS regclass)
                """
            ),
            {"name": name, "table": table},
        ),
    )
    if attached is None:
        msg = f"{name} exists but is not a partition of {table}; left alone"
        raise ValueError(msg)
    callers_timeout = cast("str", conn.scalar(_LOCK_TIMEOUT_SQL))
    callers_row_security = cast(
        "str", conn.scalar(text("SELECT current_setting('row_security')"))
    )
    # Every lock the detach and the drop need, at once and without waiting: a lock
    # request that queued would block every later reader of that table (party,
    # transaction, ...) while it waited. Anything busy fails the call with 55P03.
    locked = ", ".join(retirement_lock_set(conn, table))
    _ = conn.execute(text(f"LOCK TABLE {locked} IN ACCESS EXCLUSIVE MODE NOWAIT"))
    _ = conn.execute(_SET_LOCK_TIMEOUT_SQL, {"value": RETIRE_LOCK_TIMEOUT})
    # The owner bypasses RLS on a table that does not force it; row_security off
    # turns any policy that would still apply into an error, never a filtered count.
    _ = conn.execute(text(f"ALTER TABLE {name} NO FORCE ROW LEVEL SECURITY"))
    _ = conn.execute(text("SELECT set_config('row_security', 'off', true)"))
    holds_rows = cast("bool", conn.scalar(select(exists().select_from(table_(name)))))
    if holds_rows:
        msg = f"{name} still holds rows; delete every tenant's rows of the month first"
        raise ValueError(msg)
    _ = conn.execute(text(f"ALTER TABLE {table} DETACH PARTITION {name}"))
    _ = conn.execute(text(f"DROP TABLE {name}"))
    _ = conn.execute(_SET_LOCK_TIMEOUT_SQL, {"value": callers_timeout})
    _ = conn.execute(
        text("SELECT set_config('row_security', :value, true)"),
        {"value": callers_row_security},
    )
    logger.info(
        "%s partition %s retired",
        table,
        name,
        extra={"table": table, "partition": name, "month": f"{month:%Y-%m}"},
    )
    return True
