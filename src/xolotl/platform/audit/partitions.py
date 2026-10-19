"""Monthly partitions of ``audit_event`` with RLS and append-only triggers."""

import logging
from dataclasses import dataclass
from datetime import date
from typing import cast

from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from xolotl.platform import clock
from xolotl.platform.tenancy.ddl import rls_statements

logger = logging.getLogger(__name__)

TABLE = "audit_event"
IMMUTABLE_FUNCTION = "audit_event_immutable"
# Creating a partition takes ACCESS EXCLUSIVE on audit_event, and while that request
# waits every audit read and write queues behind it, so it gives up after this long.
LOCK_TIMEOUT = "5s"
_DECEMBER = 12
# One fixed key, so every caller (each starting process, the monthly job) takes turns.
_TURN_SQL = text(
    "SELECT pg_advisory_xact_lock(hashtext('audit_event_partitions')::bigint)"
)
_LOCK_TIMEOUT_SQL = text("SELECT current_setting('lock_timeout')")
_SET_LOCK_TIMEOUT_SQL = text("SELECT set_config('lock_timeout', :value, true)")
# A month whose partition fails with one of these SQLSTATEs is logged and skipped,
# with the log level and the reason to give; any other error propagates.
_SKIPPABLE: dict[str, tuple[int, str]] = {
    "23514": (
        logging.ERROR,
        "rows for that month sit in audit_event_default; move them out first",
    ),
    "42P07": (logging.WARNING, "a table of that name already exists"),
    "55P03": (
        logging.WARNING,
        f"audit_event stayed locked over {LOCK_TIMEOUT}; a later upgrade retries",
    ),
}


def partition_name(month: date) -> str:
    return f"{TABLE}_y{month:%Y}m{month:%m}"


def next_month(d: date) -> date:
    return date(d.year + (d.month == _DECEMBER), d.month % _DECEMBER + 1, 1)


def harden_partition(conn: Connection, name: str) -> None:
    """Force RLS on a partition and reject TRUNCATE on it.

    Partitions get no GRANT: the application role reaches rows only through the
    parent table, and the parent's row trigger is cloned onto every partition.
    """
    for statement in rls_statements(name, grant=None):
        _ = conn.execute(text(statement))
    _ = conn.execute(
        text(
            f"""
            CREATE TRIGGER {name}_no_truncate BEFORE TRUNCATE ON {name}
            FOR EACH STATEMENT EXECUTE FUNCTION {IMMUTABLE_FUNCTION}()
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
    conn: Connection, *, months_ahead: int = 3, start: date | None = None
) -> PartitionReport:
    """Create the partition for ``start``'s month and ``months_ahead`` more.

    Bounds are UTC midnights (explicit ``+00``) whatever the session ``TimeZone``, so
    partitions created from different sessions always abut.

    Callers take turns on a transaction-scoped advisory lock, so two processes that
    start together, or a start and the monthly job, never both try to create the
    same partition. Each month is created and hardened in its own savepoint, with
    ``lock_timeout`` at ``LOCK_TIMEOUT``. A month that cannot be created yet is
    logged and skipped, and the next month is tried: rows for it already sit in
    ``audit_event_default`` (23514), a table of that name exists (42P07), or
    another transaction held ``audit_event`` for too long (55P03). Any other error
    propagates. The caller's ``lock_timeout`` is left as it was.

    Returns:
        The partitions created in this call and the ones skipped, by SQLSTATE.

    """
    _ = conn.execute(_TURN_SQL)
    callers_timeout = cast("str", conn.scalar(_LOCK_TIMEOUT_SQL))
    created: list[str] = []
    skipped: dict[str, str] = {}
    month = (start or clock.now().date()).replace(day=1)
    for _ in range(months_ahead + 1):
        name = partition_name(month)
        exists = cast(
            "int | None",
            conn.scalar(
                text("SELECT 1 FROM pg_class WHERE relname = :name"), {"name": name}
            ),
        )
        if exists is None:
            skipped_by = _create_partition(conn, month, callers_timeout)
            if skipped_by is None:
                created.append(name)
            else:
                skipped[name] = skipped_by
        month = next_month(month)
    return PartitionReport(created=created, skipped=skipped)


def _create_partition(
    conn: Connection, month: date, callers_timeout: str
) -> str | None:
    """Create and harden ``month``'s partition in a savepoint.

    Returns:
        ``None`` once the partition exists, or the SQLSTATE that skipped it.

    Raises:
        DBAPIError: When creating it fails for any reason not in ``_SKIPPABLE``.

    """
    name = partition_name(month)
    upper = next_month(month)
    try:
        with conn.begin_nested():
            _ = conn.execute(_SET_LOCK_TIMEOUT_SQL, {"value": LOCK_TIMEOUT})
            _ = conn.execute(
                text(
                    f"""
                    CREATE TABLE {name} PARTITION OF {TABLE}
                    FOR VALUES FROM ('{month:%Y-%m-%d} 00:00:00+00')
                    TO ('{upper:%Y-%m-%d} 00:00:00+00')
                    """
                )
            )
            harden_partition(conn, name)
            # A setting made in a savepoint outlives its release: restore the caller's.
            _ = conn.execute(_SET_LOCK_TIMEOUT_SQL, {"value": callers_timeout})
    except DBAPIError as exc:
        sqlstate = cast("str | None", getattr(exc.orig, "sqlstate", None)) or ""
        if sqlstate not in _SKIPPABLE:
            raise
        level, reason = _SKIPPABLE[sqlstate]
        logger.log(
            level,
            "audit partition %s not created (sqlstate %s): %s",
            name,
            sqlstate,
            reason,
            extra={"partition": name, "month": f"{month:%Y-%m}", "sqlstate": sqlstate},
        )
        return sqlstate
    return None
