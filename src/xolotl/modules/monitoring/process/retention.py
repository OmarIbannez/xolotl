"""The monthly ``monitoring.retention`` job (SP-04 spec §8.6, Decision 22).

Each tenant's schedule runs it on ``XOLOTL_MONITORING_RETENTION_CRON`` (UTC). The
period is conservative (``retention_period``): the longest ``retention.transactions``
of every pack version assigned to the tenant, for any product, whose assignment had
not ended before the oldest partition month, and of every override version (ten
years once ITF was ever assigned, five under AVI alone). With no policy in force
today nothing is retired. A month is due when its end plus that period is not after
today (``policy_date``, Mexico City), so the latest row of the month has had its
full period.

**Tenants share partitions.** A monthly partition of ``transaction`` or
``txn_decision`` holds every tenant's rows of that month, and tenants may keep
them for different periods or hold a party. So a tenant never drops a partition
for itself: in its own session, under row-level security, it deletes its own rows
of a due month, in the order that keeps every foreign key satisfied, each step in
its own transaction:

1. the ``rule_hit_transaction`` links of the month's transactions;
2. the ``reportable_event`` rows of the month (a link from another month's event
   is cleared first) and the ``capture_group`` rows they leave empty, then every
   group without an event whose window ended by the month's end (a group that
   elapsed without crossing, or one a regrouping superseded after moving its
   events, plan 4b Deviation 21), a pointer to it from a superseded group
   cleared first;
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

**Legal hold.** Every step starts by locking the month's parties (those with a
transaction, a hit first seen, or a decision made in the month) ``FOR KEY SHARE``
and reading ``legal_hold``. Setting a hold takes the party row ``FOR UPDATE``, so a
hold is either committed before the step, which then stops the month (named among
the skipped months), or waits until the step commits; an ordinary edit of the
party does not wait. Steps done before the hold existed stay done. Every delete
also leaves out a held party's rows itself, so a row that reached the month after
the step took its locks (a late BACKFILL row of a party already on hold) is kept.

**Audit.** Each step that touched anything writes a ``monitoring.retention_step``
row (month, step, counts) in its own transaction, before it commits; a partition
drop and the counterparty purge write theirs in the admin transaction that does
the work. Nothing deleted is ever unaudited, whatever fails later. One
``monitoring.retention_ran`` row on the tenant sums the run up: the period, the
counts per retired month, the skipped months, the purged counterparties and the
pruned buckets. A run with no policy in force is summed up too, with ``period``
null.

**Locks.** A partition drop takes ``ACCESS EXCLUSIVE`` on the parent and on every
table referencing it or referenced by it (``retirement_lock_set``: for
``transaction`` that is ``party``, ``customer_relationship``, ``account``,
``counterparty``, ``transaction_batch``, ``policy_snapshot``, ``txn_decision``,
``rule_hit``, ``rule_hit_transaction``, ``reportable_event``,
``transaction_parked`` and ``transaction_ref``) in one ``LOCK ... NOWAIT``: if any
is busy the drop gives up at once and a later run retries, so it never queues
ahead of readers. On a busy system drops may wait for a quiet hour. The purge sets
``lock_timeout`` to ``PURGE_LOCK_TIMEOUT`` and skips rows ingest or screening
hold.

**Known limits.** Rows in a default partition belong to no listed month and are
not retired. The month filters of steps 1 to 5 use columns without an index, so
each step scans its table; fine for a monthly job, and the hold locks keep the
long window safe. A transaction of a later month loses the link to a decision made
in the retired month (step 4): the decision's own date governs it. ``_in_use``
asks every tenant for every due month, quadratic in tenants, fine at MVP scale. A
closed capture period reopened after its buckets were pruned (older than the
longest window plus the late days) would read incomplete buckets.

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
    Connection,
    CursorResult,
    Engine,
    TextClause,
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
from xolotl.platform.db import (
    SessionFactory,
    apply_context,
    bound_context,
    get_admin_engine,
    tenant_session,
)
from xolotl.platform.jobs.queue import Job
from xolotl.platform.partitions import (
    next_month,
    partition_name,
    retire_partition,
)
from xolotl.platform.policy.assignments import assignments_in_force
from xolotl.platform.policy.durations import add_iso_duration, iso_duration_days
from xolotl.platform.policy.errors import PolicyNotAssignedError
from xolotl.platform.policy.models import (
    PolicyPackVersion,
    TenantOverrideVersion,
    TenantPackAssignment,
)
from xolotl.platform.policy.overrides import TenantOverride
from xolotl.platform.policy.resolver import effective_policy
from xolotl.platform.policy.schema import PackConfig
from xolotl.platform.tenancy.context import TenantContext
from xolotl.platform.tenancy.tenants import tenant_ids

logger = logging.getLogger(__name__)

RETENTION_KIND = "monitoring.retention"
AUDIT_ACTION = "monitoring.retention_ran"
# One row per step that touched anything, in that step's own transaction.
STEP_ACTION = "monitoring.retention_step"
# The purge's locks: it never waits long for ingest or a screening job.
PURGE_LOCK_TIMEOUT = "1s"
_RECOUNT_BATCH = 500
# The decision partition goes first: transaction rows point at it.
_PARTITIONED: tuple[tuple[str, InstrumentedAttribute[datetime]], ...] = (
    ("txn_decision", TxnDecision.decided_at),
    ("transaction", Transaction.occurred_at),
)
_MONTH_PARTITION = re.compile(r"^(?:transaction|txn_decision)_y(\d{4})m(\d{2})$")
# A lock the retirement waited out, or a deadlock with a writer: a later run retries.
_RETRYABLE = frozenset({"55P03", "40P01"})
_CALENDAR_WINDOWS = {WindowKind.MONTH: "P1M", WindowKind.QUARTER: "P3M"}


_COUNTS = (
    "hit_links",
    "events",
    "event_links",
    "groups",
    "hits",
    "decision_links",
    "decisions",
    "parked",
    "refs",
    "transactions",
)


@dataclass(frozen=True, slots=True)
class RetiredMonth:
    """What retiring one month deleted for one tenant, step by step.

    ``held`` says a legal hold placed during the run stopped the month after the
    steps counted here; the month is then named among the skipped ones too.
    """

    month: str
    hit_links: int = 0
    events: int = 0
    event_links: int = 0
    groups: int = 0
    hits: int = 0
    decision_links: int = 0
    decisions: int = 0
    parked: int = 0
    refs: int = 0
    transactions: int = 0
    held: bool = False
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

    period: str | None
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


def _policy_in_force(s: Session, *, today: date) -> bool:
    products = {None} | {row.product for row in assignments_in_force(s, on=today)}
    for product in products:
        try:
            _ = effective_policy(s, on=today, product=product)
        except PolicyNotAssignedError:
            continue
        return True
    return False


def retention_period(s: Session, *, today: date, since: date) -> str | None:
    """Find the longest ``retention.transactions`` that may apply to any month kept.

    Conservative on purpose: the maximum over the pack version of every assignment,
    whatever its product, that had not ended before ``since`` (the oldest month
    the job looks at), and over every override version. A product whose
    assignment has ended, a pack that was replaced or an override that was later
    shortened still holds the months it covered. A precise per-month period can
    come later; this one only ever keeps a month longer.

    Returns:
        An ISO-8601 duration, or ``None`` when no policy is in force today.

    """
    if not _policy_in_force(s, today=today):
        return None
    periods: list[str] = []
    assignments = s.scalars(
        select(TenantPackAssignment).where(
            or_(
                TenantPackAssignment.effective_to.is_(None),
                TenantPackAssignment.effective_to >= since,
            )
        )
    )
    for assignment in assignments:
        version = s.get_one(PolicyPackVersion, assignment.pack_version_id)
        config = PackConfig.model_validate(version.config)
        periods.append(config.retention.transactions.value)
    for row in s.scalars(select(TenantOverrideVersion)):
        override = TenantOverride.model_validate(row.overrides)
        if override.retention is not None and override.retention.transactions:
            periods.append(override.retention.transactions)
    return max(periods, key=iso_duration_days, default=None)


def partition_months(s: Session) -> list[date]:
    """List the months of every monthly partition of both tables.

    The default partitions are never listed: rows there belong to no month.

    Returns:
        The first days of the months, oldest first.

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
        if match is not None:
            months.add(date(int(match.group(1)), int(match.group(2)), 1))
    return sorted(months)


def months_due(s: Session, *, today: date, period: str) -> list[date]:
    """List the months with a partition whose end plus ``period`` is not after today.

    Returns:
        The first days of the due months, oldest first.

    """
    return [
        month
        for month in partition_months(s)
        if _due(month, period=period, today=today)
    ]


# The month's parties: those with a transaction in it, a hit first seen in it, or
# a decision made in it for one of their relationships. Locked FOR KEY SHARE: a
# hold being set takes the party row FOR UPDATE (get_party(for_update=True)) and
# conflicts with it, while an ordinary edit of the party (FOR NO KEY UPDATE) does
# not wait for the step.
_MONTH_PARTIES = text(
    """
    SELECT p.legal_hold FROM party p
    WHERE p.id IN (
      SELECT t.party_id FROM transaction t
      WHERE t.occurred_at >= :lower AND t.occurred_at < :upper
      UNION
      SELECT h.party_id FROM rule_hit h
      WHERE h.first_occurred_at >= :lower AND h.first_occurred_at < :upper
      UNION
      SELECT r.party_id FROM txn_decision d
      JOIN customer_relationship r
        ON r.tenant_id = d.tenant_id AND r.id = d.relationship_id
      WHERE d.decided_at >= :lower AND d.decided_at < :upper)
    ORDER BY p.id
    FOR KEY SHARE OF p
    """
)


def held_parties_in(s: Session, month: date) -> int:
    """Lock the month's parties (key share) and count those on legal hold.

    Called first in each step's transaction: a hold committed before the step is
    seen, and a hold being placed waits until the step commits. The earlier steps'
    deletes happened before the hold existed.

    Returns:
        The number of held parties.

    """
    lower, upper = _bounds(month)
    holds = cast(
        "list[bool]",
        s.scalars(_MONTH_PARTIES, {"lower": lower, "upper": upper}).all(),
    )
    return sum(holds)


# Every delete also leaves out a held party's rows itself: a row that reached the
# month after the step locked its parties (a late BACKFILL row of a party already
# on hold) is never deleted. Each fragment names the table its statement deletes.
_TXN_NOT_HELD = """NOT EXISTS (
    SELECT 1 FROM party p WHERE p.tenant_id = transaction.tenant_id
      AND p.id = transaction.party_id AND p.legal_hold)"""
_LINKED_TXN_NOT_HELD = """NOT EXISTS (
    SELECT 1 FROM transaction t JOIN party p
      ON p.tenant_id = t.tenant_id AND p.id = t.party_id
    WHERE t.tenant_id = {table}.tenant_id AND t.occurred_at = {table}.occurred_at
      AND t.id = {table}.transaction_id AND p.legal_hold)"""
_PARTY_NOT_HELD = """NOT EXISTS (
    SELECT 1 FROM party p WHERE p.tenant_id = {table}.tenant_id
      AND p.id = {table}.party_id AND p.legal_hold)"""
_DECISION_NOT_HELD = """NOT EXISTS (
    SELECT 1 FROM customer_relationship r JOIN party p
      ON p.tenant_id = r.tenant_id AND p.id = r.party_id
    WHERE r.tenant_id = txn_decision.tenant_id
      AND r.id = txn_decision.relationship_id AND p.legal_hold)"""


def _not_held(fragment: str, table: str = "") -> TextClause:
    return text(fragment.replace("{table}", table))


def _delete_hit_links(s: Session, month: date) -> dict[str, int]:
    deleted = _rowcount(
        s.execute(
            delete(RuleHitTransaction).where(
                _within(RuleHitTransaction.occurred_at, month),
                _not_held(_LINKED_TXN_NOT_HELD, "rule_hit_transaction"),
            )
        )
    )
    return {"hit_links": deleted}


def _delete_events(s: Session, month: date) -> dict[str, int]:
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
            delete(ReportableEvent).where(
                _within(ReportableEvent.occurred_at, month),
                _not_held(_PARTY_NOT_HELD, "reportable_event"),
            )
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
    return {
        "events": events,
        "event_links": links,
        "groups": emptied + _delete_ended_groups(s, month),
    }


# A group is kept per relationship (key_kind RELATIONSHIP): a held party's
# groups stay, as its events and transactions do.
_GROUP_NOT_HELD = """NOT EXISTS (
    SELECT 1 FROM customer_relationship r JOIN party p
      ON p.tenant_id = r.tenant_id AND p.id = r.party_id
    WHERE r.tenant_id = capture_group.tenant_id
      AND capture_group.key_kind = 'RELATIONSHIP'
      AND r.id::text = capture_group.key AND p.legal_hold)"""


def _delete_ended_groups(s: Session, month: date) -> int:
    """Delete the groups without an event whose window ended by the month's end.

    A superseded group pointing at one of them loses the pointer first, so the
    ``superseded_by_id`` chain never blocks a delete.

    Returns:
        How many groups were deleted.

    """
    _, upper = _bounds(month)
    ended = list(
        s.scalars(
            select(CaptureGroup.id).where(
                CaptureGroup.window_end <= upper,
                ~exists().where(ReportableEvent.capture_group_id == CaptureGroup.id),
                text(_GROUP_NOT_HELD),
            )
        )
    )
    if not ended:
        return 0
    _ = s.execute(
        update(CaptureGroup)
        .where(CaptureGroup.superseded_by_id.in_(ended))
        .values(superseded_by_id=None)
    )
    return _rowcount(s.execute(delete(CaptureGroup).where(CaptureGroup.id.in_(ended))))


def _delete_hits(s: Session, month: date) -> dict[str, int]:
    not_held = _not_held(_PARTY_NOT_HELD, "rule_hit")
    hits = select(RuleHit.id).where(_within(RuleHit.first_occurred_at, month), not_held)
    links = _rowcount(
        s.execute(delete(RuleHitTransaction).where(RuleHitTransaction.hit_id.in_(hits)))
    )
    deleted = _rowcount(
        s.execute(
            delete(RuleHit).where(_within(RuleHit.first_occurred_at, month), not_held)
        )
    )
    return {"hits": deleted, "hit_links": links}


def _clear_decision_links(s: Session, month: date) -> dict[str, int]:
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
    return {"decision_links": decisions + transactions}


def _delete_decisions(s: Session, month: date) -> dict[str, int]:
    deleted = _rowcount(
        s.execute(
            delete(TxnDecision).where(
                _within(TxnDecision.decided_at, month),
                _not_held(_DECISION_NOT_HELD),
            )
        )
    )
    return {"decisions": deleted}


def _delete_parked(s: Session, month: date) -> dict[str, int]:
    deleted = _rowcount(
        s.execute(
            delete(TransactionParked).where(
                _within(TransactionParked.occurred_at, month),
                _not_held(_LINKED_TXN_NOT_HELD, "transaction_parked"),
            )
        )
    )
    return {"parked": deleted}


def _delete_refs(s: Session, month: date) -> dict[str, int]:
    deleted = _rowcount(
        s.execute(
            delete(TransactionRef).where(
                _within(TransactionRef.occurred_at, month),
                _not_held(_LINKED_TXN_NOT_HELD, "transaction_ref"),
            )
        )
    )
    return {"refs": deleted}


def _delete_transactions(s: Session, month: date) -> dict[str, int]:
    deleted = _rowcount(
        s.execute(
            delete(Transaction).where(
                _within(Transaction.occurred_at, month), _not_held(_TXN_NOT_HELD)
            )
        )
    )
    return {"transactions": deleted}


def _audit_on(
    conn: Connection, *, tenant_id: UUID, actor_id: str, detail: dict[str, JsonValue]
) -> None:
    """Audit a step done on an admin connection, in that connection's transaction."""
    with Session(bind=conn) as s:
        apply_context(s, TenantContext.system(tenant_id, actor_id))
        _ = record(
            s,
            AuditEvent(
                action=STEP_ACTION,
                entity_type="tenant",
                entity_id=str(tenant_id),
                detail=detail,
            ),
        )
        s.flush()


def _audit_step(s: Session, *, month: date, step: str, counts: dict[str, int]) -> None:
    """Audit one committed step in its own transaction, when it touched anything."""
    if not any(counts.values()):
        return
    detail: dict[str, JsonValue] = {"month": f"{month:%Y-%m}", "step": step}
    detail.update(counts)
    _ = record(
        s,
        AuditEvent(
            action=STEP_ACTION,
            entity_type="tenant",
            entity_id=str(bound_context(s).require_tenant_id()),
            detail=detail,
        ),
    )


def retire_month(s: Session, *, month: date) -> RetiredMonth:
    """Delete the session's tenant's rows of ``month`` in dependency order.

    Each step is its own transaction (spec Decision 22): it first locks the month's
    parties and stops the month if one is on legal hold, then deletes, then writes
    its ``monitoring.retention_step`` audit row, and commits. A failure leaves the
    earlier steps done and audited, and the next run carries on from there. The
    partitions are left to ``retire_partitions``.

    Returns:
        The rows each step deleted or unlinked; ``held`` when a hold stopped it.

    """
    steps = (
        ("hit_links", _delete_hit_links),
        ("events", _delete_events),
        ("hits", _delete_hits),
        ("decision_links", _clear_decision_links),
        ("decisions", _delete_decisions),
        ("parked", _delete_parked),
        ("refs", _delete_refs),
        ("transactions", _delete_transactions),
    )
    totals: dict[str, int] = dict.fromkeys(_COUNTS, 0)
    held = False
    for name, step in steps:
        if held_parties_in(s, month):
            s.rollback()
            held = True
            break
        counts = step(s, month)
        _audit_step(s, month=month, step=name, counts=counts)
        s.commit()
        for key, value in counts.items():
            totals[key] += value
    return RetiredMonth(
        month=f"{month:%Y-%m}",
        hit_links=totals["hit_links"],
        events=totals["events"],
        event_links=totals["event_links"],
        groups=totals["groups"],
        hits=totals["hits"],
        decision_links=totals["decision_links"],
        decisions=totals["decisions"],
        parked=totals["parked"],
        refs=totals["refs"],
        transactions=totals["transactions"],
        held=held,
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
            period = retention_period(s, today=today, since=month)
            if period is not None and not _due(month, period=period, today=today):
                return True
    return False


def _retire_one(
    admin_engine: Engine, *, table: str, month: date, actor: tuple[UUID, str]
) -> bool:
    """Retire one partition, keeping it when a row or a lock got in the way.

    The drop is audited in its own transaction, for the tenant whose run did it.

    Returns:
        Whether the partition was dropped.

    Raises:
        DBAPIError: When the database fails for any reason but a lock timeout or a
            deadlock.

    """
    tenant_id, actor_id = actor
    try:
        with admin_engine.begin() as conn:
            dropped = retire_partition(conn, table=table, month=month)
            if dropped:
                _audit_on(
                    conn,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    detail={
                        "month": f"{month:%Y-%m}",
                        "step": "partitions",
                        "partition": partition_name(table, month),
                    },
                )
            return dropped
    except ValueError as exc:
        logger.warning(
            "partition kept: %s",
            exc,
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
    session_factory: SessionFactory,
    admin_engine: Engine,
    *,
    month: date,
    today: date,
    actor: tuple[UUID, str],
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
        if _retire_one(admin_engine, table=table, month=month, actor=actor):
            dropped.append(partition_name(table, month))
    return tuple(dropped)


# The purge runs as the owner with the tenant bound, so row-level security still
# scopes every statement to the tenant on a server where it binds the owner, and
# every statement names the tenant as well.
_BIND_TENANT = text("SELECT set_config('app.tenant_id', :tenant, true)")
_SET_LOCK_TIMEOUT = text("SELECT set_config('lock_timeout', :value, true)")
# The recount locks a page of counterparties whose count is off, in id order and
# skipping any an ingest holds, then counts in a new statement: its snapshot is
# taken after the locks, so an increment committed meanwhile is counted, and an
# ingest that comes later waits and adds to the fresh count.
_RECOUNT_PAGE = text(
    """
    SELECT c.id FROM counterparty c
    WHERE c.tenant_id = :tenant AND c.id > :after
      AND c.txn_count <> (
        SELECT count(*) FROM transaction t
        WHERE t.tenant_id = c.tenant_id AND t.counterparty_id = c.id)
    ORDER BY c.id LIMIT :limit
    FOR UPDATE SKIP LOCKED
    """
)
_RECOUNT = text(
    """
    UPDATE counterparty c SET txn_count = (
      SELECT count(*) FROM transaction t
      WHERE t.tenant_id = c.tenant_id AND t.counterparty_id = c.id)
    WHERE c.tenant_id = :tenant AND c.id = ANY(:ids)
    """
)
_CANDIDATES = text(
    """
    SELECT id FROM counterparty
    WHERE tenant_id = :tenant AND txn_count = 0
    ORDER BY id FOR UPDATE SKIP LOCKED
    """
)
_SUBJECTS = text(
    """
    SELECT counterparty_id FROM screening_subject
    WHERE tenant_id = :tenant AND counterparty_id = ANY(:ids)
    """
)
_LOCK_SUBJECTS = text(
    """
    SELECT counterparty_id FROM screening_subject
    WHERE tenant_id = :tenant AND counterparty_id = ANY(:ids)
    ORDER BY id FOR UPDATE SKIP LOCKED
    """
)
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
# The purge's statements in dependency order, each with the count it reports.
_PURGE: tuple[tuple[str | None, TextClause], ...] = (
    (
        None,
        text(
            """
        UPDATE counterparty SET screening_subject_id = NULL
        WHERE tenant_id = :tenant AND id = ANY(:ids)
        """
        ),
    ),
    (
        "allow_list",
        text(
            """
        DELETE FROM screening_whitelist
        WHERE tenant_id = :tenant AND subject_id = ANY(:subjects)
        """
        ),
    ),
    (
        "dispositions",
        text(
            """
        DELETE FROM match_disposition
        WHERE tenant_id = :tenant AND match_id IN (
          SELECT id FROM screening_match
          WHERE tenant_id = :tenant AND subject_id = ANY(:subjects))
        """
        ),
    ),
    (
        "screening_hits",
        text(
            """
        DELETE FROM screening_hit
        WHERE tenant_id = :tenant AND (
          run_id IN (
            SELECT id FROM screening_run
            WHERE tenant_id = :tenant AND subject_id = ANY(:subjects))
          OR match_id IN (
            SELECT id FROM screening_match
            WHERE tenant_id = :tenant AND subject_id = ANY(:subjects)))
        """
        ),
    ),
    (
        "findings",
        text(
            """
        DELETE FROM screening_match
        WHERE tenant_id = :tenant AND subject_id = ANY(:subjects)
        """
        ),
    ),
    (
        "runs",
        text(
            """
        DELETE FROM screening_run
        WHERE tenant_id = :tenant AND subject_id = ANY(:subjects)
        """
        ),
    ),
    (
        "subjects",
        text(
            """
        DELETE FROM screening_subject
        WHERE tenant_id = :tenant AND id = ANY(:subjects)
        """
        ),
    ),
    (
        "name_index",
        text(
            """
        DELETE FROM counterparty_name_index
        WHERE tenant_id = :tenant AND counterparty_id = ANY(:ids)
        """
        ),
    ),
    (
        "counterparties",
        text("DELETE FROM counterparty WHERE tenant_id = :tenant AND id = ANY(:ids)"),
    ),
)


def recount_counterparties(admin_engine: Engine, *, tenant_id: UUID) -> int:
    """Set ``txn_count`` from ``transaction`` where it is off, a page per commit.

    Returns:
        How many counterparties were recounted.

    """
    after = UUID(int=0)
    recounted = 0
    while True:
        with admin_engine.begin() as conn:
            _ = conn.execute(_BIND_TENANT, {"tenant": str(tenant_id)})
            _ = conn.execute(_SET_LOCK_TIMEOUT, {"value": PURGE_LOCK_TIMEOUT})
            ids = cast(
                "list[UUID]",
                conn.scalars(
                    _RECOUNT_PAGE,
                    {"tenant": tenant_id, "after": after, "limit": _RECOUNT_BATCH},
                ).all(),
            )
            if ids:
                _ = conn.execute(_RECOUNT, {"tenant": tenant_id, "ids": ids})
        recounted += len(ids)
        if len(ids) < _RECOUNT_BATCH:
            return recounted
        after = ids[-1]


def _purge(
    conn: Connection, *, tenant_id: UUID, period: str, now: datetime, actor_id: str
) -> int:
    candidates = cast(
        "list[UUID]", conn.scalars(_CANDIDATES, {"tenant": tenant_id}).all()
    )
    if not candidates:
        return 0
    params: dict[str, object] = {"tenant": tenant_id, "ids": candidates}
    with_subject = set(cast("list[UUID]", conn.scalars(_SUBJECTS, params).all()))
    locked = set(cast("list[UUID]", conn.scalars(_LOCK_SUBJECTS, params).all()))
    # A subject a screening job holds keeps its counterparty for a later run.
    free = [c for c in candidates if c not in with_subject or c in locked]
    if not free:
        return 0
    rows = conn.execute(
        _ORPHANS, {"tenant": tenant_id, "ids": free, "period": period, "now": now}
    ).all()
    ids = [cast("UUID", row[0]) for row in rows]
    subjects = [cast("UUID", row[1]) for row in rows if row[1] is not None]
    if not ids:
        return 0
    detail: dict[str, JsonValue] = {"step": "counterparties"}
    for name, statement in _PURGE:
        result = conn.execute(
            statement, {"tenant": tenant_id, "ids": ids, "subjects": subjects}
        )
        if name is not None:
            detail[name] = _rowcount(result)
    # In the purge's own transaction: what it deleted is audited or not deleted.
    _audit_on(conn, tenant_id=tenant_id, actor_id=actor_id, detail=detail)
    return len(ids)


def purge_counterparties(
    admin_engine: Engine,
    *,
    tenant_id: UUID,
    period: str,
    now: datetime,
    actor_id: str,
) -> int:
    """Purge the tenant's counterparties that nothing references any more.

    ``txn_count`` is recounted first (``recount_counterparties``). In one
    transaction with ``lock_timeout`` at ``PURGE_LOCK_TIMEOUT``, the candidates are
    locked skipping those an ingest holds, their subjects are locked skipping
    those a screening job holds (that counterparty waits for a later run), and a
    candidate is purged only if, read again after the locks, no transaction
    references it and none of its screening records is younger than ``period``.
    A lock that still times out, or a deadlock, purges nothing this run.

    Returns:
        How many counterparties were purged.

    Raises:
        DBAPIError: When the database fails for another reason.

    """
    try:
        _ = recount_counterparties(admin_engine, tenant_id=tenant_id)
        with admin_engine.begin() as conn:
            _ = conn.execute(_BIND_TENANT, {"tenant": str(tenant_id)})
            _ = conn.execute(_SET_LOCK_TIMEOUT, {"value": PURGE_LOCK_TIMEOUT})
            return _purge(
                conn, tenant_id=tenant_id, period=period, now=now, actor_id=actor_id
            )
    except DBAPIError as exc:
        sqlstate = cast("str | None", getattr(exc.orig, "sqlstate", None))
        if sqlstate not in _RETRYABLE:
            raise
        logger.warning(
            "counterparty purge deferred: a lock was busy; a later run retries",
            extra={"tenant_id": str(tenant_id), "sqlstate": sqlstate},
        )
        return 0


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
        """Retire the tenant's due months, purge counterparties, prune buckets.

        Every step that deletes is audited in its own transaction
        (``monitoring.retention_step``); one ``monitoring.retention_ran`` row sums
        the run up, also when no policy is in force and nothing could be retired.
        """
        now = clock.now()
        today = policy_date(now)
        actor_id = bound_context(session).actor_id
        months = partition_months(session)
        since = months[0] if months else today.replace(day=1)
        period = retention_period(session, today=today, since=since)
        retired: list[RetiredMonth] = []
        skipped: list[str] = []
        purged = pruned = 0
        if period is None:
            logger.info(
                "retention retires nothing: no policy in force",
                extra={"tenant_id": str(job.tenant_id), "on": today.isoformat()},
            )
        else:
            admin_engine = get_admin_engine()
            for month in months_due(session, today=today, period=period):
                done = retire_month(session, month=month)
                if done.held:
                    skipped.append(f"{month:%Y-%m}")
                else:
                    done = replace(
                        done,
                        partitions_dropped=retire_partitions(
                            self._session_factory,
                            admin_engine,
                            month=month,
                            today=today,
                            actor=(job.tenant_id, actor_id),
                        ),
                    )
                if done.deleted_anything():
                    retired.append(done)
            purged = purge_counterparties(
                admin_engine,
                tenant_id=job.tenant_id,
                period=period,
                now=now,
                actor_id=actor_id,
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
