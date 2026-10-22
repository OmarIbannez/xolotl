"""What processing feeds the pure evaluator: aggregate reads and the profile (§9.5).

``reader_for`` adapts an ``AggregateStore`` to the evaluator's ``AggregateReader``
for one transaction and one rule's declarations: a ``DURATION`` declaration reads
the window ending at the instant asked for, a ``MONTH`` or ``QUARTER`` one the
Mexico City calendar bucket holding it, an ``ACCUM`` one the accumulator holding
it. A value whose backfill has not run reads as ``INCOMPLETE``. C5's decide path
uses the same reader over a memory store.

``profile_for`` reads what the evaluator knows of the customer, the relationship's
declared profile included (plan 4c C8, read with the relationship). The party's
screening status lives in the screening engine, which monitoring may not import
(contract ``monitoring-boundaries``), so the composition root installs a reader of
it with ``use_screening_status``; without one the status is unknown (None).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    WindowKind,
)
from xolotl.modules.monitoring.aggregates.store import (
    EMPTY,
    AggregateKey,
    AggregateStore,
    AggregateValue,
)
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.rules.assignments import LoadedRule, RuleSet
from xolotl.modules.monitoring.rules.kinds import (
    INCOMPLETE,
    DeclaredProfile,
    EvalContext,
    EvalProfile,
    Firing,
    Incomplete,
    evaluate,
)
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction
from xolotl.modules.party.service import pep_status_of, relationship_ref
from xolotl.platform.policy import EffectivePolicy

_MONTHS_PER_QUARTER = 3
_MONTHS_PER_YEAR = 12

# Reads a party's screening status (a SubjectStatus value), None when never screened.
type ScreeningStatusReader = Callable[[Session, UUID], str | None]

_screening_status: list[ScreeningStatusReader] = []


def use_screening_status(reader: ScreeningStatusReader | None) -> None:
    """Install the reader of a party's screening status; None removes it."""
    _screening_status.clear()
    if reader is not None:
        _screening_status.append(reader)


def _period_key(window_kind: WindowKind, at: datetime) -> str:
    local = at.astimezone(MEXICO_CITY_ZONE)
    if window_kind is WindowKind.MONTH:
        return f"{local.year}-{local.month:02d}"
    return f"{local.year}-Q{(local.month - 1) // _MONTHS_PER_QUARTER + 1}"


@dataclass(slots=True)
class StoreReader:
    """Reads one transaction's declared aggregates through a store, memoised."""

    store: AggregateStore
    txn: EnrichedTransaction
    declarations: Mapping[str, AggregateDeclaration]
    memo: dict[tuple[str, datetime | None], AggregateValue | Incomplete] = field(
        default_factory=dict[tuple[str, datetime | None], AggregateValue | Incomplete]
    )

    def read(
        self, name: str, *, at: datetime | None = None
    ) -> AggregateValue | Incomplete:
        found = self.memo.get((name, at))
        if found is None:
            found = self._read(name, at)
            self.memo[name, at] = found
        return found

    def _read(self, name: str, at: datetime | None) -> AggregateValue | Incomplete:
        declaration = self.declarations.get(name)
        if declaration is None:
            return INCOMPLETE
        key = self.txn.key_for(declaration.key_kind)
        if key is None:
            # The transaction has no such key, so nothing of it was counted.
            return EMPTY
        aggregate_key = AggregateKey(declaration.key_kind, key)
        when = self.txn.occurred_at if at is None else at
        match declaration.window_kind:
            case WindowKind.DURATION:
                value = self.store.read(aggregate_key, declaration, at=when)
            case WindowKind.MONTH | WindowKind.QUARTER:
                value = self.store.calendar_read(
                    aggregate_key,
                    declaration,
                    period_key=_period_key(declaration.window_kind, when),
                )
            case WindowKind.ACCUM:
                value = self.store.accumulator_read(aggregate_key, declaration, at=when)
        return value if value.complete else INCOMPLETE


def reader_for(
    store: AggregateStore,
    txn: EnrichedTransaction,
    declarations: Mapping[str, AggregateDeclaration],
) -> StoreReader:
    """Build the evaluator's reader of ``declarations`` (by name) for ``txn``.

    Returns:
        A reader whose reads are memoised per name and instant; an undeclared name
        or an incomplete value reads as ``INCOMPLETE``.

    """
    return StoreReader(store, txn, declarations)


def months_between(first: datetime, then: datetime) -> int:
    """Count the whole calendar months from ``first`` to ``then``.

    Returns:
        The months elapsed, 0 when ``then`` is less than a month after ``first``.

    """
    months = (then.year - first.year) * _MONTHS_PER_YEAR + then.month - first.month
    if (then.day, then.time()) < (first.day, first.time()):
        months -= 1
    return max(months, 0)


def _minor(value: JsonValue) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _declared(stored: dict[str, JsonValue] | None) -> DeclaredProfile | None:
    # The JSON form party's DeclaredProfile stores; a row without a currency
    # declares nothing comparable.
    if stored is None:
        return None
    currency = stored.get("currency")
    if not isinstance(currency, str):
        return None
    return DeclaredProfile(
        monthly_inflow_minor=_minor(stored.get("monthly_inflow_minor")),
        monthly_outflow_minor=_minor(stored.get("monthly_outflow_minor")),
        currency=currency,
    )


def profile_with_history(
    s: Session, txn: EnrichedTransaction, *, history_months: int
) -> EvalProfile:
    """Read the customer's profile at ``txn`` with ``history_months`` given.

    The simulation counts history months from its own stream (plan 4b Deviation
    13), so it reads everything else here and skips the history query. The
    declared profile is the relationship's, None until set; processing and
    simulation both read it here.

    Returns:
        The profile.

    """
    relationship = relationship_ref(s, txn.relationship_id)
    status = _screening_status[0](s, txn.party_id) if _screening_status else None
    return EvalProfile(
        tier=relationship.account_tier,
        risk_rating=txn.risk_rating,
        monitoring_profile=txn.monitoring_profile,
        pep_status=pep_status_of(s, txn.party_id).value,
        party_screening_status=status,
        relationship_started_at=relationship.started_at,
        declared_profile=_declared(relationship.declared_profile),
        history_months=history_months,
    )


def profile_for(s: Session, txn: EnrichedTransaction) -> EvalProfile:
    """Read what the evaluator knows of the transaction's customer.

    ``history_months`` counts from the relationship's earliest processed
    transaction; the declared profile is the relationship's, None until set.

    Returns:
        The profile.

    """
    earliest = s.scalar(
        select(func.min(Transaction.occurred_at)).where(
            Transaction.relationship_id == txn.relationship_id,
            Transaction.status == TransactionStatus.PROCESSED,
        )
    )
    return profile_with_history(
        s,
        txn,
        history_months=0
        if earliest is None
        else months_between(earliest, txn.occurred_at),
    )


def evaluate_rules(
    ruleset: RuleSet,
    *,
    txn: EnrichedTransaction,
    store: AggregateStore,
    profile: EvalProfile,
    policy: EffectivePolicy,
    now: datetime,
) -> list[tuple[LoadedRule, Firing]]:
    """Evaluate every rule of ``ruleset`` for ``txn``, in rule code order.

    Each rule reads its own declarations by its own names through ``store``, since
    two rules may give one name to different declarations.

    Returns:
        The rules that fired, with their firings.

    """
    fired: list[tuple[LoadedRule, Firing]] = []
    for loaded in sorted(ruleset.rules, key=lambda rule: rule.rule_code):
        declared = {item.name: item for item in loaded.definition.aggregates}
        ctx = EvalContext(
            txn=txn,
            aggregates=reader_for(store, txn, declared),
            profile=profile,
            policy=policy,
            lists=ruleset.lists,
            now=now,
        )
        firing = evaluate(loaded.definition, ctx)
        if firing is not None:
            fired.append((loaded, firing))
    return fired
