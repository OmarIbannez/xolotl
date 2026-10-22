"""The monthly ``monitoring.retention`` job (SP-04 spec §8.6, Decision 22).

Each tenant's schedule runs it on ``XOLOTL_MONITORING_RETENTION_CRON`` (UTC). The
period is the longest ``retention.transactions`` of every policy in force for the
tenant today (ten years under ITF, five under AVI); with no policy in force nothing
is retired. A month is due when its end plus that period is not after today
(``policy_date``, Mexico City), so the latest row of the month has had its full
period.

**Tenants share partitions.** A monthly partition of ``transaction`` or
``txn_decision`` holds every tenant's rows of that month, and tenants may keep
them for different periods or hold a party. So a tenant never drops a partition
for itself: in its own session, under row-level security, it deletes its own rows
of a due month, in the order that keeps every foreign key satisfied, each step in
its own transaction:

1. the ``rule_hit_transaction`` links of the month's transactions;
2. the ``reportable_event`` rows of the month (a link from another month's event
   is cleared first) and the ``capture_group`` rows they leave empty;
3. the ``rule_hit`` rows whose ``first_occurred_at`` is in the month, with every
   link of those hits (a ``WINDOW`` or ``COOLDOWN`` link may point at a later
   month);
4. the decision links: ``txn_decision.transaction_id`` and ``occurred_at`` of the
   decisions that point at the month's transactions, and ``transaction.decision_id``
   and ``decided_at`` of the month's transactions and of every transaction whose
   decision was made in the month;
5. the ``txn_decision`` rows decided in the month;
6. the ``transaction_parked`` rows, 7. the ``transaction_ref`` rows and 8. the
   ``transaction`` rows of the month.

Every step deletes by month, so a run that stopped half-way is finished by the
next one. Then, through the admin engine, the month's partitions are detached and
dropped (``retire_partition``) only once every tenant is done with them: in each
tenant's own session, no row of the month is left and the tenant's own period has
passed for it; then ``retire_partition`` looks again, under the lock, at what is
in the partition whoever's it is. A partition still holding another tenant's rows
(a longer period, a held party, no policy) stays until that tenant retires them.

**Legal hold.** A month is skipped, and named in the audit row, while a party under
legal hold has a transaction, a hit or a decision in it.

**Counterparties** (plan 4c Deviation 11). A counterparty that no transaction
references any more is purged with its screening subject, runs, hits, findings,
dispositions, allow-list rows and name-index row, through the admin engine (the
application role may not delete ``match_disposition``), with the tenant bound and
named in every statement. A counterparty any of whose screening records is still
inside the period is kept: a finding or a disposition is a record of its own,
whatever the age of the transactions behind it.

**Buckets.** ``agg_bucket`` and ``agg_distinct`` rows whose bucket ended before the
longest declared window plus ``XOLOTL_MONITORING_LATE_DAYS`` (a late transaction
still reads that far back) are deleted, except accumulators still running and
month or quarter buckets that an open or reopened capture period overlaps.
``agg_declaration`` is append-only and is never touched.

One audit row, ``monitoring.retention_ran`` on the tenant, carries the counts of
every step per retired month, the skipped months, the purged counterparties and
the pruned buckets.
"""

import logging
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    ColumnElement,
    CursorResult,
    Engine,
    and_,
    delete,
    exists,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import InstrumentedAttribute, Session

from xolotl.modules.monitoring.aggregates.declarations import WindowKind
from xolotl.modules.monitoring.aggregates.models import AggDeclaration
from xolotl.modules.monitoring.captures.models import CaptureGroup, ReportableEvent
from xolotl.modules.monitoring.decide.models import TxnDecision
from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.rules.models import RuleHit, RuleHitTransaction
from xolotl.modules.monitoring.txn.models import (
    Transaction,
    TransactionParked,
    TransactionRef,
)
from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.config import get_settings
from xolotl.platform.db import SessionFactory, get_admin_engine, tenant_session
from xolotl.platform.jobs.queue import Job
from xolotl.platform.partitions import (
    next_month,
    partition_name,
    retire_partition,
)
from xolotl.platform.policy.assignments import assignments_in_force
from xolotl.platform.policy.durations import add_iso_duration, iso_duration_days
from xolotl.platform.policy.errors import PolicyNotAssignedError
from xolotl.platform.policy.resolver import effective_policy
from xolotl.platform.tenancy.context import TenantContext
from xolotl.platform.tenancy.tenants import tenant_ids

logger = logging.getLogger(__name__)

RETENTION_KIND = "monitoring.retention"
AUDIT_ACTION = "monitoring.retention_ran"
# The decision partition goes first: transaction rows point at it.
_PARTITIONED: tuple[tuple[str, InstrumentedAttribute[datetime]], ...] = (
    ("txn_decision", TxnDecision.decided_at),
    ("transaction", Transaction.occurred_at),
)
_MONTH_PARTITION = re.compile(r"^(?:transaction|txn_decision)_y(\d{4})m(\d{2})$")
# A lock the retirement waited out, or a deadlock with a writer: a later run retries.
_RETRYABLE = frozenset({"55P03", "40P01"})
_CALENDAR_WINDOWS = {WindowKind.MONTH: "P1M", WindowKind.QUARTER: "P3M"}


@dataclass(frozen=True, slots=True)
class RetiredMonth:
    """What retiring one month deleted for one tenant, step by step."""

    month: str
    hit_links: int
    events: int
    event_links: int
    groups: int
    hits: int
    decision_links: int
    decisions: int
    parked: int
    refs: int
    transactions: int
    partitions_dropped: tuple[str, ...] = ()

    def deleted_anything(self) -> bool:
        return bool(self.partitions_dropped) or any((
            self.hit_links,
            self.events,
            self.event_links,
            self.groups,
            self.hits,
            self.decision_links,
            self.decisions,
            self.parked,
            self.refs,
            self.transactions,
        ))

    def detail(self) -> dict[str, JsonValue]:
        return {
            "month": self.month,
            "hit_links": self.hit_links,
            "events": self.events,
            "event_links": self.event_links,
            "groups": self.groups,
            "hits": self.hits,
            "decision_links": self.decision_links,
            "decisions": self.decisions,
            "parked": self.parked,
            "refs": self.refs,
            "transactions": self.transactions,
            "partitions_dropped": list(self.partitions_dropped),
        }


@dataclass(frozen=True, slots=True)
class RetentionReport:
    """One run for one tenant: the audit row's detail."""

    period: str
    retired: tuple[RetiredMonth, ...]
    skipped: tuple[str, ...]
    counterparties_purged: int
    buckets_pruned: int

    def detail(self) -> dict[str, JsonValue]:
        return {
            "period": self.period,
            "retired": [month.detail() for month in self.retired],
            "skipped": list(self.skipped),
            "counterparties_purged": self.counterparties_purged,
            "buckets_pruned": self.buckets_pruned,
        }


def _bounds(month: date) -> tuple[datetime, datetime]:
    # The partition bounds: UTC midnights, as ensure_partitions creates them.
    upper = next_month(month)
    return (
        datetime(month.year, month.month, 1, tzinfo=UTC),
        datetime(upper.year, upper.month, 1, tzinfo=UTC),
    )


def _within(
    column: ColumnElement[datetime]
    | InstrumentedAttribute[datetime]
    | InstrumentedAttribute[datetime | None],
    month: date,
) -> ColumnElement[bool]:
    lower, upper = _bounds(month)
    return and_(column >= lower, column < upper)


def _rowcount(result: object) -> int:
    # A DML statement's result is a cursor result; its rowcount is what it touched.
    if not isinstance(result, CursorResult):
        msg = "a DML statement returned no cursor result"
        raise TypeError(msg)
    return result.rowcount


def retention_period(s: Session, *, today: date) -> str | None:
    """Find the longest ``retention.transactions`` of the policies in force.

    Every product with an assignment in force today is resolved, and the tenant-wide
    policy too; the longest period wins, so no product's rows go early.

    Returns:
        An ISO-8601 duration, or ``None`` when no policy is in force.

    """
    products = {None} | {row.product for row in assignments_in_force(s, on=today)}
    periods: list[str] = []
    for product in sorted(products, key=lambda value: value or ""):
        try:
            policy = effective_policy(s, on=today, product=product)
        except PolicyNotAssignedError:
            continue
        periods.append(policy.retention.transactions.value)
    return max(periods, key=iso_duration_days, default=None)


def months_due(s: Session, *, today: date, period: str) -> list[date]:
    """List the months with a partition whose end plus ``period`` is not after today.

    The default partitions are never listed: rows there belong to no month.

    Returns:
        The first days of the due months, oldest first.

    """
    names = s.scalars(
        text(
            """
            SELECT c.relname FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            WHERE i.inhparent IN ('transaction'::regclass, 'txn_decision'::regclass)
            """
        )
    ).all()
    months: set[date] = set()
    for name in cast("list[str]", names):
        match = _MONTH_PARTITION.match(name)
        if match is None:
            continue
        month = date(int(match.group(1)), int(match.group(2)), 1)
        if _due(month, period=period, today=today):
            months.add(month)
    return sorted(months)


def held_parties_in(s: Session, month: date) -> int:
    """Count the tenant's parties on legal hold with a row in ``month``.

    A transaction of the month, a hit first seen in it, or a decision made in it
    for one of the party's relationships, each counts.

    Returns:
        The number of held parties.

    """
    lower, upper = _bounds(month)
    held = cast(
        "int | None",
        s.scalar(
            text(
                """
            SELECT count(*) FROM party p
            WHERE p.legal_hold AND (
              EXISTS (
                SELECT 1 FROM transaction t
                WHERE t.tenant_id = p.tenant_id AND t.party_id = p.id
                  AND t.occurred_at >= :lower AND t.occurred_at < :upper)
              OR EXISTS (
                SELECT 1 FROM rule_hit h
                WHERE h.tenant_id = p.tenant_id AND h.party_id = p.id
                  AND h.first_occurred_at >= :lower AND h.first_occurred_at < :upper)
              OR EXISTS (
                SELECT 1 FROM txn_decision d
                JOIN customer_relationship r
                  ON r.tenant_id = d.tenant_id AND r.id = d.relationship_id
                WHERE r.tenant_id = p.tenant_id AND r.party_id = p.id
                  AND d.decided_at >= :lower AND d.decided_at < :upper))
            """
            ),
            {"lower": lower, "upper": upper},
        ),
    )
    return held or 0


def _delete_hit_links(s: Session, month: date) -> int:
    return _rowcount(
        s.execute(
            delete(RuleHitTransaction).where(
                _within(RuleHitTransaction.occurred_at, month)
            )
        )
    )


def _delete_events(s: Session, month: date) -> tuple[int, int, int]:
    in_month = select(ReportableEvent.id).where(
        _within(ReportableEvent.occurred_at, month)
    )
    groups = set(
        s.scalars(
            select(ReportableEvent.capture_group_id)
            .where(
                _within(ReportableEvent.occurred_at, month),
                ReportableEvent.capture_group_id.is_not(None),
            )
            .distinct()
        )
    )
    outside = ~_within(ReportableEvent.occurred_at, month)
    links = _rowcount(
        s.execute(
            update(ReportableEvent)
            .where(outside, ReportableEvent.supersedes_id.in_(in_month))
            .values(supersedes_id=None)
        )
    ) + _rowcount(
        s.execute(
            update(ReportableEvent)
            .where(outside, ReportableEvent.superseded_by_id.in_(in_month))
            .values(superseded_by_id=None)
        )
    )
    events = _rowcount(
        s.execute(
            delete(ReportableEvent).where(_within(ReportableEvent.occurred_at, month))
        )
    )
    emptied = 0
    if groups:
        successor = CaptureGroup.__table__.alias("successor")
        emptied = _rowcount(
            s.execute(
                delete(CaptureGroup).where(
                    CaptureGroup.id.in_(groups),
                    ~exists().where(
                        ReportableEvent.capture_group_id == CaptureGroup.id
                    ),
                    ~exists().where(successor.c.superseded_by_id == CaptureGroup.id),
                )
            )
        )
    return events, links, emptied


def _delete_hits(s: Session, month: date) -> tuple[int, int]:
    hits = select(RuleHit.id).where(_within(RuleHit.first_occurred_at, month))
    links = _rowcount(
        s.execute(delete(RuleHitTransaction).where(RuleHitTransaction.hit_id.in_(hits)))
    )
    deleted = _rowcount(
        s.execute(delete(RuleHit).where(_within(RuleHit.first_occurred_at, month)))
    )
    return deleted, links


def _clear_decision_links(s: Session, month: date) -> int:
    decisions = _rowcount(
        s.execute(
            update(TxnDecision)
            .where(
                TxnDecision.occurred_at.is_not(None),
                _within(TxnDecision.occurred_at, month),
            )
            .values(transaction_id=None, occurred_at=None)
        )
    )
    transactions = _rowcount(
        s.execute(
            update(Transaction)
            .where(
                Transaction.decision_id.is_not(None),
                or_(
                    _within(Transaction.occurred_at, month),
                    _within(Transaction.decided_at, month),
                ),
            )
            .values(decision_id=None, decided_at=None)
        )
    )
    return decisions + transactions


def _delete_decisions(s: Session, month: date) -> int:
    return _rowcount(
        s.execute(delete(TxnDecision).where(_within(TxnDecision.decided_at, month)))
    )


def _delete_parked(s: Session, month: date) -> int:
    return _rowcount(
        s.execute(
            delete(TransactionParked).where(
                _within(TransactionParked.occurred_at, month)
            )
        )
    )


def _delete_refs(s: Session, month: date) -> int:
    return _rowcount(
        s.execute(
            delete(TransactionRef).where(_within(TransactionRef.occurred_at, month))
        )
    )


def _delete_transactions(s: Session, month: date) -> int:
    return _rowcount(
        s.execute(delete(Transaction).where(_within(Transaction.occurred_at, month)))
    )


def retire_month(s: Session, *, month: date) -> RetiredMonth:
    """Delete the session's tenant's rows of ``month`` in dependency order.

    Each step commits on its own (spec Decision 22): a failure leaves the earlier
    steps done and the next run carries on from there. The partitions are left to
    ``retire_partitions``.

    Returns:
        The rows each step deleted or unlinked.

    """
    hit_links = _delete_hit_links(s, month)
    s.commit()
    events, event_links, groups = _delete_events(s, month)
    s.commit()
    hits, hit_links_of_hits = _delete_hits(s, month)
    s.commit()
    decision_links = _clear_decision_links(s, month)
    s.commit()
    decisions = _delete_decisions(s, month)
    s.commit()
    parked = _delete_parked(s, month)
    s.commit()
    refs = _delete_refs(s, month)
    s.commit()
    transactions = _delete_transactions(s, month)
    s.commit()
    return RetiredMonth(
        month=f"{month:%Y-%m}",
        hit_links=hit_links + hit_links_of_hits,
        events=events,
        event_links=event_links,
        groups=groups,
        hits=hits,
        decision_links=decision_links,
        decisions=decisions,
        parked=parked,
        refs=refs,
        transactions=transactions,
    )


def _due(month: date, *, period: str, today: date) -> bool:
    return add_iso_duration(next_month(month), period) <= today


def _in_use(
    session_factory: SessionFactory,
    *,
    column: InstrumentedAttribute[datetime],
    month: date,
    today: date,
) -> bool:
    """Whether some tenant still has rows of ``month``, or may still keep them.

    Each tenant is asked in its own session, as the application role, so row-level
    security answers for that tenant and no bypass is needed. A tenant whose period
    has not passed for the month keeps the partition even without rows in it.

    Returns:
        ``True`` at the first tenant with a row or a period still running.

    """
    probe = select(exists().where(_within(column, month)))
    for tenant_id in tenant_ids(session_factory, actor_id=RETENTION_KIND):
        context = TenantContext.system(tenant_id, RETENTION_KIND)
        with tenant_session(context, factory=session_factory) as s:
            if s.scalar(probe):
                return True
            period = retention_period(s, today=today)
            if period is not None and not _due(month, period=period, today=today):
                return True
    return False


def _retire_one(admin_engine: Engine, *, table: str, month: date) -> bool:
    """Retire one partition, keeping it when a row or a lock got in the way.

    Returns:
        Whether the partition was dropped.

    Raises:
        DBAPIError: When the database fails for any reason but a lock timeout or a
            deadlock.

    """
    try:
        with admin_engine.begin() as conn:
            return retire_partition(conn, table=table, month=month)
    except ValueError:
        logger.warning(
            "partition kept: rows arrived before it was retired",
            extra={"table": table, "month": f"{month:%Y-%m}"},
        )
    except DBAPIError as exc:
        sqlstate = cast("str | None", getattr(exc.orig, "sqlstate", None))
        if sqlstate not in _RETRYABLE:
            raise
        logger.warning(
            "partition kept: its table stayed locked; a later run retries",
            extra={"table": table, "month": f"{month:%Y-%m}", "sqlstate": sqlstate},
        )
    return False


def retire_partitions(
    session_factory: SessionFactory, admin_engine: Engine, *, month: date, today: date
) -> tuple[str, ...]:
    """Drop ``month``'s partitions of both tables once every tenant is done with them.

    Every tenant's period must have passed for the month and no tenant may have a
    row left in it. A partition another tenant still uses, one a writer kept locked
    past the lock timeout, or one that gained a row since the look, is left for a
    later run.

    Returns:
        The names of the partitions dropped, decisions first.

    """
    dropped: list[str] = []
    for table, column in _PARTITIONED:
        if _in_use(session_factory, column=column, month=month, today=today):
            continue
        if _retire_one(admin_engine, table=table, month=month):
            dropped.append(partition_name(table, month))
    return tuple(dropped)


# The purge runs as the owner with the tenant bound, so row-level security still
# scopes every statement to the tenant on a server where it binds the owner, and
# every statement names the tenant as well.
_BIND_TENANT = text("SELECT set_config('app.tenant_id', :tenant, true)")
_RECOUNT = text(
    """
    UPDATE counterparty c SET txn_count = counted.n
    FROM (
      SELECT c2.id, (
        SELECT count(*) FROM transaction t
        WHERE t.tenant_id = c2.tenant_id AND t.counterparty_id = c2.id) AS n
      FROM counterparty c2 WHERE c2.tenant_id = :tenant) AS counted
    WHERE c.tenant_id = :tenant AND c.id = counted.id AND c.txn_count <> counted.n
    """
)
_CANDIDATES = text(
    """
    SELECT id FROM counterparty
    WHERE tenant_id = :tenant AND txn_count = 0
    ORDER BY id FOR UPDATE SKIP LOCKED
    """
)
_LOCK_SUBJECTS = text(
    """
    SELECT id FROM screening_subject
    WHERE tenant_id = :tenant AND counterparty_id = ANY(:ids)
    FOR UPDATE
    """
)
# A fresh statement after the locks: no transaction references the row, and no
# screening record of its subject is still inside the period.
_ORPHANS = text(
    """
    SELECT c.id, s.id AS subject_id FROM counterparty c
    LEFT JOIN screening_subject s
      ON s.tenant_id = c.tenant_id AND s.counterparty_id = c.id
    WHERE c.tenant_id = :tenant AND c.id = ANY(:ids)
      AND NOT EXISTS (
        SELECT 1 FROM transaction t
        WHERE t.tenant_id = c.tenant_id AND t.counterparty_id = c.id)
      AND NOT EXISTS (
        SELECT 1 FROM screening_run r
        WHERE r.tenant_id = c.tenant_id AND r.subject_id = s.id
          AND r.created_at + CAST(:period AS interval) > :now)
      AND NOT EXISTS (
        SELECT 1 FROM screening_match m
        WHERE m.tenant_id = c.tenant_id AND m.subject_id = s.id
          AND GREATEST(m.created_at, m.updated_at, m.closed_at)
              + CAST(:period AS interval) > :now)
      AND NOT EXISTS (
        SELECT 1 FROM match_disposition d
        JOIN screening_match m ON m.tenant_id = d.tenant_id AND m.id = d.match_id
        WHERE d.tenant_id = c.tenant_id AND m.subject_id = s.id
          AND d.decided_at + CAST(:period AS interval) > :now)
      AND NOT EXISTS (
        SELECT 1 FROM screening_whitelist w
        WHERE w.tenant_id = c.tenant_id AND w.subject_id = s.id
          AND GREATEST(w.created_at, w.revoked_at)
              + CAST(:period AS interval) > :now)
    """
)
_PURGE = tuple(
    text(statement)
    for statement in (
        """
        UPDATE counterparty SET screening_subject_id = NULL
        WHERE tenant_id = :tenant AND id = ANY(:ids)
        """,
        """
        DELETE FROM screening_whitelist
        WHERE tenant_id = :tenant AND subject_id = ANY(:subjects)
        """,
        """
        DELETE FROM match_disposition
        WHERE tenant_id = :tenant AND match_id IN (
          SELECT id FROM screening_match
          WHERE tenant_id = :tenant AND subject_id = ANY(:subjects))
        """,
        """
        DELETE FROM screening_hit
        WHERE tenant_id = :tenant AND (
          run_id IN (
            SELECT id FROM screening_run
            WHERE tenant_id = :tenant AND subject_id = ANY(:subjects))
          OR match_id IN (
            SELECT id FROM screening_match
            WHERE tenant_id = :tenant AND subject_id = ANY(:subjects)))
        """,
        """
        DELETE FROM screening_match
        WHERE tenant_id = :tenant AND subject_id = ANY(:subjects)
        """,
        """
        DELETE FROM screening_run
        WHERE tenant_id = :tenant AND subject_id = ANY(:subjects)
        """,
        """
        DELETE FROM screening_subject
        WHERE tenant_id = :tenant AND id = ANY(:subjects)
        """,
        """
        DELETE FROM counterparty_name_index
        WHERE tenant_id = :tenant AND counterparty_id = ANY(:ids)
        """,
        "DELETE FROM counterparty WHERE tenant_id = :tenant AND id = ANY(:ids)",
    )
)


def purge_counterparties(
    admin_engine: Engine, *, tenant_id: UUID, period: str, now: datetime
) -> int:
    """Purge the tenant's counterparties that nothing references any more.

    ``txn_count`` is recounted from ``transaction`` first. A candidate is locked,
    with its subject, so no transaction or run can start pointing at it, and is
    purged only if, read again after the locks, no transaction references it and
    none of its screening records is younger than ``period``.

    Returns:
        How many counterparties were purged.

    """
    tenant = {"tenant": str(tenant_id)}
    with admin_engine.begin() as conn:
        _ = conn.execute(_BIND_TENANT, tenant)
        _ = conn.execute(_RECOUNT, {"tenant": tenant_id})
        candidates = list(conn.scalars(_CANDIDATES, {"tenant": tenant_id}))
        if not candidates:
            return 0
        _ = conn.execute(_LOCK_SUBJECTS, {"tenant": tenant_id, "ids": candidates})
        rows = conn.execute(
            _ORPHANS,
            {"tenant": tenant_id, "ids": candidates, "period": period, "now": now},
        ).all()
        ids = [cast("UUID", row[0]) for row in rows]
        subjects = [cast("UUID", row[1]) for row in rows if row[1] is not None]
        if not ids:
            return 0
        for statement in _PURGE:
            _ = conn.execute(
                statement, {"tenant": tenant_id, "ids": ids, "subjects": subjects}
            )
    return len(ids)


def _longest_window(s: Session) -> str | None:
    windows: list[str] = []
    for declaration in s.scalars(select(AggDeclaration)):
        calendar = _CALENDAR_WINDOWS.get(declaration.window_kind)
        windows.append(calendar or declaration.window)
    return max(windows, key=iso_duration_days, default=None)


# A bucket is stale once its end (its start plus its kind's length; an accumulator
# stores its own) is before the longest window plus the late days. Running
# accumulators and month or quarter buckets an open or reopened capture period
# overlaps stay; a bucket's members in agg_distinct go with it.
_PRUNE = text(
    """
    WITH stale AS (
      SELECT b.tenant_id, b.key_kind, b.key, b.agg_id, b.bucket_start
      FROM agg_bucket b
      CROSS JOIN LATERAL (
        SELECT CASE b.bucket_kind
          WHEN 'HOUR' THEN b.bucket_start + interval '1 hour'
          WHEN 'DAY' THEN b.bucket_start + interval '1 day'
          WHEN 'MONTH' THEN b.bucket_start + interval '1 month'
          WHEN 'QUARTER' THEN b.bucket_start + interval '3 months'
          ELSE b.bucket_end
        END AS ends) AS e
      WHERE e.ends < CAST(:now AS timestamptz) - CAST(:window AS interval)
                     - make_interval(days => :late_days)
        AND NOT (b.bucket_kind = 'ACCUM' AND b.bucket_end >= :now)
        AND NOT (b.bucket_kind IN ('MONTH', 'QUARTER') AND EXISTS (
          SELECT 1 FROM capture_period p
          WHERE p.tenant_id = b.tenant_id AND p.status IN ('OPEN', 'REOPENED')
            AND p.period_start
                < CAST(e.ends AT TIME ZONE 'America/Mexico_City' AS date)
            AND p.period_end
                >= CAST(b.bucket_start AT TIME ZONE 'America/Mexico_City' AS date)))
    ), members AS (
      DELETE FROM agg_distinct d USING stale
      WHERE d.tenant_id = stale.tenant_id AND d.key_kind = stale.key_kind
        AND d.key = stale.key AND d.agg_id = stale.agg_id
        AND d.bucket_start = stale.bucket_start
    )
    DELETE FROM agg_bucket b USING stale
    WHERE b.tenant_id = stale.tenant_id AND b.key_kind = stale.key_kind
      AND b.key = stale.key AND b.agg_id = stale.agg_id
      AND b.bucket_start = stale.bucket_start
    """
)


def prune_buckets(s: Session, *, now: datetime) -> int:
    """Delete the session's tenant's buckets that no declared window reads any more.

    Returns:
        How many ``agg_bucket`` rows were deleted (their ``agg_distinct`` members
        go with them).

    """
    window = _longest_window(s)
    if window is None:
        return 0
    params = {
        "now": now,
        "window": window,
        "late_days": get_settings().monitoring_late_days,
    }
    return _rowcount(s.execute(_PRUNE, params))


class RetentionJob:
    """The handler of ``monitoring.retention``, one tenant per job."""

    def __init__(self, *, session_factory: SessionFactory) -> None:
        self._session_factory: SessionFactory = session_factory

    def run(self, session: Session, job: Job) -> None:
        """Retire the tenant's due months, purge counterparties, prune buckets."""
        now = clock.now()
        today = policy_date(now)
        period = retention_period(session, today=today)
        if period is None:
            logger.info(
                "retention skipped: no policy in force",
                extra={"tenant_id": str(job.tenant_id), "on": today.isoformat()},
            )
            return
        admin_engine = get_admin_engine()
        retired: list[RetiredMonth] = []
        skipped: list[str] = []
        for month in months_due(session, today=today, period=period):
            if held_parties_in(session, month):
                skipped.append(f"{month:%Y-%m}")
                continue
            done = retire_month(session, month=month)
            dropped = retire_partitions(
                self._session_factory, admin_engine, month=month, today=today
            )
            done = replace(done, partitions_dropped=dropped)
            if done.deleted_anything():
                retired.append(done)
        purged = purge_counterparties(
            admin_engine, tenant_id=job.tenant_id, period=period, now=now
        )
        pruned = prune_buckets(session, now=now)
        report = RetentionReport(
            period=period,
            retired=tuple(retired),
            skipped=tuple(skipped),
            counterparties_purged=purged,
            buckets_pruned=pruned,
        )
        _ = record(
            session,
            AuditEvent(
                action=AUDIT_ACTION,
                entity_type="tenant",
                entity_id=str(job.tenant_id),
                detail=report.detail(),
            ),
        )
        logger.info(
            "retention ran",
            extra={
                "tenant_id": str(job.tenant_id),
                "months_retired": [month.month for month in retired],
                "months_skipped": skipped,
                "counterparties_purged": purged,
                "buckets_pruned": pruned,
            },
        )
