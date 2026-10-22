"""Rule hits: one per version and dedup key, with cooldown and evidence (spec §9.6).

A firing becomes a ``rule_hit`` keyed ``<key_kind>:<key>:<bucket>``, the bucket
being ``occurred_at`` floored in UTC to ``dedup.window`` (or the transaction's
Mexico City month for ``MONTH``). A second firing in the same bucket links its
transaction to that hit (``WINDOW``) and counts it; a firing after the key's latest
hit but before that hit's ``cooldown_until`` links as ``COOLDOWN``; anything else
inserts a hit with its evidence: the triggering transaction, the window's matching
transactions (500 at most, newest first) and the aggregate values the rule read.

Durations floor from 0001-01-01 00:00 UTC, a Monday, so a ``P7D`` bucket is a
Monday-to-Sunday UTC week and ``P1D`` a UTC day; a whole number of months or
years floors to calendar months. The insert is ``ON CONFLICT DO NOTHING`` on
``(tenant_id, rule_version_id, dedup_key)``, so two sessions firing at once make
one hit and the loser links to it. Evidence holds ids, amounts, units and codes,
never a name (PRV-501), and ``monitoring.rule_hit_created.v1`` is staff-only.
"""

import calendar
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import ColumnElement, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import matches
from xolotl.modules.monitoring.aggregates.windows import bucket_start_for
from xolotl.modules.monitoring.enrich.service import enriched_from_row
from xolotl.modules.monitoring.enums import BucketKind, TransactionStatus
from xolotl.modules.monitoring.events import RULE_HIT_CREATED, publish_monitoring_event
from xolotl.modules.monitoring.rules.assignments import LoadedRule
from xolotl.modules.monitoring.rules.definitions import MONTH, Dedup
from xolotl.modules.monitoring.rules.enums import HitTransactionRole
from xolotl.modules.monitoring.rules.kinds import Firing
from xolotl.modules.monitoring.rules.models import RuleHit, RuleHitTransaction
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction, KeyKind
from xolotl.platform.db import bound_context
from xolotl.platform.ids import uuid7
from xolotl.platform.policy.durations import add_iso_duration, iso_duration_days

EVIDENCE_LIMIT = 500
EVIDENCE_VERSION = 1
# A Monday at midnight UTC: P7D buckets are ISO weeks.
_ANCHOR = datetime(1, 1, 1, tzinfo=UTC)
_DURATION = re.compile(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?$")
_MONTHS_PER_YEAR = 12
# A month inside a mixed months-and-days dedup window, on the 360-day scale.
_DAYS_PER_MONTH = 30


def _months_and_days(window: str) -> tuple[int, int]:
    match = _DURATION.match(window)
    if match is None:
        msg = f"{window!r} is not a duration of years, months and days"
        raise ValueError(msg)
    years, months, days = (int(part or 0) for part in match.groups())
    return years * _MONTHS_PER_YEAR + months, days


def dedup_bucket(txn: EnrichedTransaction, dedup: Dedup) -> str:
    """Name the dedup bucket ``txn`` falls in.

    Returns:
        The transaction's month key for ``MONTH``, else the ISO instant of
        ``occurred_at`` floored in UTC to the window.

    """
    if dedup.window == MONTH:
        return txn.period_keys.MONTH
    months, days = _months_and_days(dedup.window)
    at = txn.occurred_at.astimezone(UTC)
    if months and not days:
        index = at.year * _MONTHS_PER_YEAR + at.month - 1
        year, month = divmod(index - index % months, _MONTHS_PER_YEAR)
        return datetime(year, month + 1, 1, tzinfo=UTC).isoformat()
    span = timedelta(days=months * _DAYS_PER_MONTH + days)
    if span <= timedelta(0):
        # The validator refuses a zero window; a stored one buckets per instant.
        return at.isoformat()
    return (at - (at - _ANCHOR) % span).isoformat()


def dedup_key_for(firing: Firing, txn: EnrichedTransaction, dedup: Dedup) -> str:
    """Build the hit's dedup key.

    Returns:
        ``"<key_kind>:<key>:<bucket>"``.

    """
    return f"{firing.key_kind.value}:{firing.key}:{dedup_bucket(txn, dedup)}"


def _evidence_start(window: str, end: datetime) -> datetime:
    if window == MONTH:
        return bucket_start_for(BucketKind.MONTH, end)
    months, days = _months_and_days(window)
    end = end.astimezone(UTC)
    year, month = divmod(
        end.year * _MONTHS_PER_YEAR + end.month - 1 - months, _MONTHS_PER_YEAR
    )
    day = min(end.day, calendar.monthrange(year, month + 1)[1])
    return end.replace(year=year, month=month + 1, day=day) - timedelta(days=days)


def _of_key(firing: Firing, txn: EnrichedTransaction) -> ColumnElement[bool]:
    match firing.key_kind:
        case KeyKind.PARTY:
            return Transaction.party_id == txn.party_id
        case KeyKind.RELATIONSHIP:
            return Transaction.relationship_id == txn.relationship_id
        case KeyKind.ACCOUNT:
            return Transaction.account_id == txn.account_id
        case KeyKind.COUNTERPARTY:
            return Transaction.counterparty_id == txn.counterparty_id
        case KeyKind.DEVICE:
            return Transaction.device_hash == bytes.fromhex(firing.key)


def evidence_transactions(
    s: Session,
    rule: LoadedRule,
    firing: Firing,
    txn: EnrichedTransaction,
    *,
    in_flight: Sequence[EnrichedTransaction] = (),
) -> list[tuple[UUID, datetime]]:
    """List the window's transactions of the firing's key.

    The window is ``evidence.transactions_window`` before ``txn.occurred_at``
    (the trigger itself excluded); rows must match ``evidence.transactions_where``.
    They are the key's processed rows plus ``in_flight``: the row being
    processed, not yet ``PROCESSED`` while a late row's re-evaluation runs.

    Returns:
        Up to 500 ``(id, occurred_at)`` pairs, newest first; none when the rule
        names no window.

    """
    evidence = rule.definition.evidence
    if evidence.transactions_window is None:
        return []
    start = _evidence_start(evidence.transactions_window, txn.occurred_at)
    pending = [
        (item.occurred_at, item.id)
        for item in in_flight
        if item.id != txn.id
        and start <= item.occurred_at <= txn.occurred_at
        and item.key_for(firing.key_kind) == firing.key
        and matches(evidence.transactions_where, item)
    ]
    rows = s.scalars(
        select(Transaction)
        .where(
            _of_key(firing, txn),
            Transaction.status == TransactionStatus.PROCESSED,
            Transaction.occurred_at >= start,
            Transaction.occurred_at < txn.occurred_at,
            Transaction.id != txn.id,
        )
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
    )
    found: list[tuple[datetime, UUID]] = []
    for row in rows:
        if matches(evidence.transactions_where, enriched_from_row(row)):
            found.append((row.occurred_at, row.id))
            if len(found) == EVIDENCE_LIMIT:
                break
    newest = sorted([*pending, *found], reverse=True)[:EVIDENCE_LIMIT]
    return [(row_id, occurred_at) for occurred_at, row_id in newest]


def link_transaction(
    s: Session,
    hit_id: UUID,
    rows: Sequence[tuple[UUID, datetime]],
    role: HitTransactionRole,
) -> int:
    """Link transactions to a hit; a transaction already linked keeps its role.

    Returns:
        How many links this call inserted.

    """
    if not rows:
        return 0
    tenant_id = bound_context(s).require_tenant_id()
    inserted = s.scalars(
        insert(RuleHitTransaction)
        .values([
            {
                "tenant_id": tenant_id,
                "hit_id": hit_id,
                "transaction_id": transaction_id,
                "occurred_at": occurred_at,
                "role": role,
            }
            for transaction_id, occurred_at in rows
        ])
        .on_conflict_do_nothing()
        .returning(RuleHitTransaction.transaction_id)
    ).all()
    return len(inserted)


def _existing(s: Session, rule: LoadedRule, dedup_key: str) -> UUID | None:
    return s.scalar(
        select(RuleHit.id).where(
            RuleHit.rule_version_id == rule.rule_version_id,
            RuleHit.dedup_key == dedup_key,
        )
    )


def _count_firing(s: Session, hit_id: UUID, txn: EnrichedTransaction) -> None:
    # A transaction counts once: a re-evaluation or a replay of one already
    # linked adds nothing (B7 review I1).
    linked = [(txn.id, txn.occurred_at)]
    if not link_transaction(s, hit_id, linked, HitTransactionRole.WINDOW):
        return
    _ = s.execute(
        update(RuleHit)
        .where(RuleHit.id == hit_id)
        .values(fired_count=RuleHit.fired_count + 1)
    )


def _cooling(
    s: Session, rule: LoadedRule, firing: Firing, txn: EnrichedTransaction
) -> UUID | None:
    # The key's latest hit at or before this firing, if its cooldown still runs.
    latest = s.execute(
        select(RuleHit.id, RuleHit.cooldown_until)
        .where(
            RuleHit.rule_version_id == rule.rule_version_id,
            RuleHit.key_kind == firing.key_kind,
            RuleHit.key == firing.key,
            RuleHit.first_occurred_at <= txn.occurred_at,
        )
        .order_by(RuleHit.first_occurred_at.desc())
        .limit(1)
    ).first()
    if latest is None:
        return None
    hit_id, cooldown_until = latest
    if cooldown_until is None or cooldown_until <= txn.occurred_at:
        return None
    return hit_id


def _evidence(firing: Firing, lists_hash: bytes) -> dict[str, JsonValue]:
    aggregates: dict[str, JsonValue] = {
        name: {
            "value": str(snapshot.value),
            "unit": snapshot.unit,
            "window": snapshot.window,
        }
        for name, snapshot in firing.aggregates.items()
    }
    params: dict[str, JsonValue] = dict(firing.params)
    return {
        "v": EVIDENCE_VERSION,
        "aggregates": aggregates,
        "params": params,
        "explanation": firing.explanation,
        "lists_hash": lists_hash.hex(),
    }


def record_firing(
    s: Session,
    rule: LoadedRule,
    firing: Firing,
    *,
    txn: EnrichedTransaction,
    lists_hash: bytes,
    in_flight: Sequence[EnrichedTransaction] = (),
) -> RuleHit | None:
    """Turn a firing into a hit, or into a link to the hit it repeats.

    ``in_flight`` names the row being processed when ``txn`` is a later row
    re-evaluated for it, so a new hit's evidence includes it (B7 review I2).

    Returns:
        The new hit, or None when the firing was linked to an existing hit
        (``WINDOW`` in its dedup bucket, ``COOLDOWN`` inside its cooldown).

    """
    dedup = rule.definition.dedup
    dedup_key = dedup_key_for(firing, txn, dedup)
    existing = _existing(s, rule, dedup_key)
    if existing is not None:
        _count_firing(s, existing, txn)
        return None
    cooling = _cooling(s, rule, firing, txn)
    if cooling is not None:
        _ = link_transaction(
            s, cooling, [(txn.id, txn.occurred_at)], HitTransactionRole.COOLDOWN
        )
        return None
    cooldown = (
        None
        if iso_duration_days(dedup.cooldown) == 0
        else datetime.combine(
            add_iso_duration(txn.occurred_at.astimezone(UTC).date(), dedup.cooldown),
            txn.occurred_at.astimezone(UTC).timetz(),
        )
    )
    hit_id = s.scalar(
        insert(RuleHit)
        .values(
            tenant_id=bound_context(s).require_tenant_id(),
            id=uuid7(),
            rule_version_id=rule.rule_version_id,
            rule_code=rule.rule_code,
            key_kind=firing.key_kind,
            key=firing.key,
            dedup_key=dedup_key,
            party_id=txn.party_id,
            relationship_id=txn.relationship_id,
            severity=firing.severity.value,
            outcome=firing.outcome.value,
            window_start=firing.window_start,
            window_end=firing.window_end,
            evidence=_evidence(firing, lists_hash),
            cooldown_until=cooldown,
            policy_snapshot_id=txn.policy_snapshot_id,
            definition_hash=rule.definition_hash,
            assignment_id=rule.assignment_id,
            first_transaction_id=txn.id,
            first_occurred_at=txn.occurred_at,
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "rule_version_id", "dedup_key"]
        )
        .returning(RuleHit.id)
    )
    if hit_id is None:
        # A concurrent session inserted it first: this firing repeats that hit.
        winner = _existing(s, rule, dedup_key)
        if winner is not None:
            _count_firing(s, winner, txn)
        return None
    _ = link_transaction(
        s, hit_id, [(txn.id, txn.occurred_at)], HitTransactionRole.TRIGGER
    )
    window = evidence_transactions(s, rule, firing, txn, in_flight=in_flight)
    _ = link_transaction(
        s,
        hit_id,
        window,
        HitTransactionRole.WINDOW,
    )
    hit = s.get_one(RuleHit, (bound_context(s).require_tenant_id(), hit_id))
    _ = publish_monitoring_event(
        s,
        RULE_HIT_CREATED,
        entity="rule_hit",
        entity_id=hit_id,
        key=str(txn.party_id),
        data={
            "hit_id": str(hit_id),
            "rule_version_id": str(rule.rule_version_id),
            "rule_code": rule.rule_code,
            "party_id": str(txn.party_id),
            "relationship_id": str(txn.relationship_id),
            "severity": firing.severity.value,
            "outcome": firing.outcome.value,
            "window_start": firing.window_start.isoformat(),
            "window_end": firing.window_end.isoformat(),
        },
        policy_snapshot_id=txn.policy_snapshot_id,
    )
    return hit
