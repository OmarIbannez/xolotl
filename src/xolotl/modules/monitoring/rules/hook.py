"""The rules processing hook: ALERT rules on every processed row (spec §8.5 step 2).

``RulesHook`` runs inside the row's database transaction, after the row was added
to its aggregates and under the party's processing lock, so the hits, links and
notices it writes commit or roll back with the row. It takes no lock of its own
beyond the rows it writes (``rule_hit`` by its dedup key, ``tier_limit_notice``):
the party lock, then the tenant's aggregate epoch lock, then these rows, as every
drain does, so it adds no lock order a backfill could invert.

A late row also re-evaluates the party's later processed rows whose windows now
contain it (``reevaluate_later``); dedup and cooldown make repeats harmless, so
only firings that were not recorded yet become hits.
"""

import calendar
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    WindowKind,
    window_parts,
)
from xolotl.modules.monitoring.aggregates.store import AggregateStore
from xolotl.modules.monitoring.aggregates.windows import (
    bucket_kind_for,
    bucket_start_for,
)
from xolotl.modules.monitoring.enrich.policy import SnapshotRef, policy_date
from xolotl.modules.monitoring.enrich.service import enriched_from_row
from xolotl.modules.monitoring.enums import BucketKind, TransactionStatus
from xolotl.modules.monitoring.process.hooks import ProcessingContext
from xolotl.modules.monitoring.rules.assignments import (
    LoadedRule,
    RuleSet,
    active_rules,
)
from xolotl.modules.monitoring.rules.definitions import RuleKind
from xolotl.modules.monitoring.rules.enums import NoticeSource
from xolotl.modules.monitoring.rules.evaluate import evaluate_rules, profile_for
from xolotl.modules.monitoring.rules.hits import record_firing
from xolotl.modules.monitoring.rules.tier_limits import (
    LIMIT_CODE_MONTHLY_OPS,
    notice_tier_limit,
)
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction
from xolotl.platform import clock
from xolotl.platform.db import bound_context
from xolotl.platform.policy import EffectivePolicy
from xolotl.platform.policy.snapshots import get_snapshot

if TYPE_CHECKING:
    from uuid import UUID

_MONTHS_PER_YEAR = 12


def _plus_months(at: datetime, months: int) -> datetime:
    year, month = divmod(at.year * _MONTHS_PER_YEAR + at.month - 1 + months, 12)
    day = min(at.day, calendar.monthrange(year, month + 1)[1])
    return at.replace(year=year, month=month + 1, day=day)


def _reach(declaration: AggregateDeclaration, at: datetime) -> datetime:
    """Return the last instant whose window of ``declaration`` contains ``at``.

    Returns:
        ``at`` plus the window for a ``DURATION`` or ``ACCUM`` declaration, the
        end of the calendar bucket holding ``at`` for ``MONTH`` and ``QUARTER``.

    """
    at = at.astimezone(UTC)
    if declaration.window_kind in {WindowKind.MONTH, WindowKind.QUARTER}:
        kind = bucket_kind_for(declaration)
        months = 1 if kind is BucketKind.MONTH else 3
        return _plus_months(bucket_start_for(kind, at), months)
    months, days, hours = window_parts(declaration.window) or (0, 0, 0)
    return _plus_months(at, months) + timedelta(days=days, hours=hours)


def _evaluate(
    s: Session,
    txn: EnrichedTransaction,
    ruleset: RuleSet,
    *,
    store: AggregateStore,
    policy: EffectivePolicy,
    in_flight: Sequence[EnrichedTransaction] = (),
) -> None:
    if not ruleset.rules:
        return
    profile = profile_for(s, txn)
    fired = evaluate_rules(
        ruleset,
        txn=txn,
        store=store,
        profile=profile,
        policy=policy,
        now=clock.now(),
    )
    for loaded, firing in fired:
        _ = record_firing(
            s,
            loaded,
            firing,
            txn=txn,
            lists_hash=ruleset.lists.content_hash,
            in_flight=in_flight,
        )
        if loaded.definition.kind is RuleKind.TIER_LIMIT and profile.tier:
            # Idempotent per relationship and month: the first path to see the
            # overrun publishes the one webhook (spec §12.2).
            _ = notice_tier_limit(
                s,
                relationship_id=txn.relationship_id,
                account_id=txn.account_id,
                tier=profile.tier,
                limit_code=LIMIT_CODE_MONTHLY_OPS,
                period_key=txn.period_keys.MONTH,
                outcome=firing.outcome,
                source=NoticeSource.PROCESS,
            )


# No window reaches further than six calendar months: a DURATION or ACCUM window
# is at most P6M, and a QUARTER bucket ends within three (A7's limits).
_LONGEST_REACH_MONTHS = 6


def reevaluate_later(
    s: Session, late: EnrichedTransaction, ctx: ProcessingContext
) -> int:
    """Re-evaluate the party's processed rows whose windows now hold ``late``.

    Every later row of the party (one at the same instant included, ``late``
    itself never) within six months is a candidate. It is re-evaluated under the
    rules in force on its own date that read an aggregate and whose reach (the
    end of the rule's longest window from ``late.occurred_at``, for that version)
    holds the row, with the policy of its own snapshot. So a rule that started
    after ``late``'s date, or a newer version with a longer window, still re-fires
    (B7 re-review N2). A new hit links ``late`` as evidence.

    Returns:
        The number of rows re-evaluated.

    """
    tenant_id = bound_context(s).require_tenant_id()
    horizon = _plus_months(late.occurred_at.astimezone(UTC), _LONGEST_REACH_MONTHS)
    # Only the keys first: most candidates are left out by the reach test.
    candidates = s.execute(
        select(Transaction.occurred_at, Transaction.id, Transaction.product)
        .where(
            Transaction.party_id == late.party_id,
            Transaction.status == TransactionStatus.PROCESSED,
            Transaction.occurred_at >= late.occurred_at,
            Transaction.occurred_at <= horizon,
            Transaction.id != late.id,
        )
        .order_by(Transaction.occurred_at, Transaction.id)
    ).all()
    reaches: dict[UUID, datetime] = {}
    policies: dict[UUID, EffectivePolicy] = {
        ctx.snapshot.snapshot_id: ctx.snapshot.policy
    }
    evaluated = 0
    for occurred_at, row_id, product in candidates:
        in_force = active_rules(s, on=policy_date(occurred_at), product=product)
        reached: list[LoadedRule] = []
        for loaded in in_force.rules:
            if not loaded.definition.aggregates:
                # Another row never changes whether a transaction fires it.
                continue
            until = reaches.get(loaded.rule_version_id)
            if until is None:
                until = max(
                    _reach(item, late.occurred_at)
                    for item in loaded.definition.aggregates
                )
                reaches[loaded.rule_version_id] = until
            if occurred_at <= until:
                reached.append(loaded)
        if not reached:
            continue
        row = s.get_one(Transaction, (tenant_id, occurred_at, row_id))
        txn = enriched_from_row(row)
        policy = policies.get(txn.policy_snapshot_id)
        if policy is None:
            policy = get_snapshot(s, txn.policy_snapshot_id).policy
            policies[txn.policy_snapshot_id] = policy
        rules = replace(in_force, rules=tuple(reached))
        _evaluate(s, txn, rules, store=ctx.store, policy=policy, in_flight=(late,))
        evaluated += 1
    return evaluated


class RulesHook:
    """Plan 4a's ``ProcessingHook`` for the tenant's ALERT rules."""

    name: str = "rules"

    @staticmethod
    def declarations(
        s: Session, snapshot: SnapshotRef, *, on: date, product: str | None
    ) -> Sequence[AggregateDeclaration]:
        """Name the aggregates of the rules in force on ``on`` for ``product``.

        Returns:
            The rule set's declarations, one per ``agg_id``.

        """
        del snapshot
        return active_rules(s, on=on, product=product).declarations

    @staticmethod
    def evaluate(s: Session, txn: EnrichedTransaction, ctx: ProcessingContext) -> None:
        """Evaluate the rules in force on the row's date and record the firings."""
        ruleset = active_rules(s, on=policy_date(txn.occurred_at), product=txn.product)
        _evaluate(s, txn, ruleset, store=ctx.store, policy=ctx.snapshot.policy)
        if ctx.late:
            _ = reevaluate_later(s, txn, ctx)
