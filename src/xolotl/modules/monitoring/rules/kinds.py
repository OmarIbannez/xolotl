"""The pure evaluator: one function per ALERT kind (spec §9.1).

``evaluate(definition, ctx)`` answers whether one transaction fires one rule. It
reads the rule's declared aggregates through ``ctx.aggregates`` (the Postgres
store in processing, a memory store in simulation and on the decide path, so
simulation equals production by construction, spec Decision 7), and an aggregate
whose history is not backfilled yet (``INCOMPLETE``, spec §8.4) never yields a
firing: a hit from a partial window would be a false alert and a missed dedup.

Pure: no database, no web framework (contract ``monitoring-evaluator-is-pure``).
"""

import calendar
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol, final, override

from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    Measure,
    WindowKind,
    matches,
    window_parts,
)
from xolotl.modules.monitoring.aggregates.store import AggregateValue
from xolotl.modules.monitoring.aggregates.windows import (
    bucket_kind_for,
    bucket_start_for,
    window_bounds,
)
from xolotl.modules.monitoring.enums import BucketKind
from xolotl.modules.monitoring.rules.definitions import (
    AnyParam,
    HitOutcome,
    RuleDefinition,
    RuleFamily,
    RuleKind,
    Severity,
)
from xolotl.modules.monitoring.txn.schema import (
    Direction,
    EnrichedTransaction,
    KeyKind,
)
from xolotl.platform.policy.jurisdictions import JurisdictionLists
from xolotl.platform.policy.resolver import EffectivePolicy

# PROFILE_DEVIATION compares with history only from three months of it (§9.1),
# and its trailing window holds six.
_MIN_HISTORY_MONTHS = 3
_TRAILING_MONTHS = 6
# DORMANT_REACTIVATION with no earlier activity is a reactivation only for a key
# with six months of history (otherwise the customer is new, not dormant).
_DORMANT_HISTORY_MONTHS = 6
_MONTHS_PER_YEAR = 12
_LAST_SEEN = "last_seen"


@final
class Incomplete:
    """The read of an aggregate whose window's history is not backfilled yet."""

    __slots__ = ()

    @override
    def __repr__(self) -> str:
        return "INCOMPLETE"


INCOMPLETE = Incomplete()


class AggregateReader(Protocol):
    """Reads a rule's declared aggregates for the transaction's key."""

    def read(
        self, name: str, *, at: datetime | None = None
    ) -> AggregateValue | Incomplete:
        """Read the declaration named ``name`` over its window ending at ``at``.

        ``at`` defaults to the transaction's ``occurred_at``; the dormancy gate
        reads ``last_seen`` before the rule's window.
        """
        ...


@dataclass(frozen=True, slots=True)
class DeclaredProfile:
    """The customer's declared monthly volumes, in minor units of ``currency``."""

    monthly_inflow_minor: int | None
    monthly_outflow_minor: int | None
    currency: str


@dataclass(frozen=True, slots=True)
class EvalProfile:
    """What the evaluator knows of the customer beyond the transaction.

    ``pep_status`` is a ``PepStatus`` value, ``party_screening_status`` a
    ``SubjectStatus`` value or None when the party was never screened.
    """

    tier: str | None
    risk_rating: str | None
    monitoring_profile: str
    pep_status: str
    party_screening_status: str | None
    relationship_started_at: datetime | None
    declared_profile: DeclaredProfile | None
    history_months: int


@dataclass(frozen=True, slots=True)
class EvalContext:
    """The evaluator's whole input besides the definition."""

    txn: EnrichedTransaction
    aggregates: AggregateReader
    profile: EvalProfile
    policy: EffectivePolicy
    lists: JurisdictionLists
    now: datetime


@dataclass(frozen=True, slots=True)
class AggregateSnapshot:
    """A declared value at firing time; ``unit`` is the unit key of a unit sum."""

    value: Decimal
    unit: str | None
    window: str
    count: int


@dataclass(frozen=True, slots=True)
class Firing:
    """One rule fired for one transaction's key over one window."""

    rule_code: str
    key_kind: KeyKind
    key: str
    window_start: datetime
    window_end: datetime
    severity: Severity
    outcome: HitOutcome
    aggregates: Mapping[str, AggregateSnapshot]
    params: Mapping[str, str]
    explanation: str


@dataclass(slots=True)
class _Reads:
    """Reads a definition's aggregates and remembers what it read.

    A read of an undeclared name returns None; an incomplete read also returns
    None and marks the evaluation incomplete, so it cannot fire.
    """

    definition: RuleDefinition
    reader: AggregateReader
    incomplete: bool = False
    read: dict[str, tuple[AggregateDeclaration, AggregateValue]] = field(
        default_factory=dict[str, tuple[AggregateDeclaration, AggregateValue]]
    )

    def declared(self, name: str) -> AggregateDeclaration | None:
        return next(
            (item for item in self.definition.aggregates if item.name == name), None
        )

    def value(self, name: str, *, at: datetime | None = None) -> AggregateValue | None:
        declaration = self.declared(name)
        if declaration is None:
            return None
        found = self.reader.read(name, at=at)
        if isinstance(found, Incomplete) or not found.complete:
            self.incomplete = True
            return None
        self.read[name] = (declaration, found)
        return found

    def number(self, name: str) -> Decimal | None:
        declaration = self.declared(name)
        found = self.value(name)
        if declaration is None or found is None:
            return None
        return _number(declaration, found)


def _number(declaration: AggregateDeclaration, value: AggregateValue) -> Decimal | None:
    measured = value.of(declaration.function)
    if isinstance(measured, int):
        return Decimal(measured)
    return measured if isinstance(measured, Decimal) else None


def _decimal(definition: RuleDefinition, name: str) -> Decimal | None:
    param = definition.params.get(name)
    value = None if param is None else param.value
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return Decimal(value)
    return value if isinstance(value, Decimal) else None


def _integer(definition: RuleDefinition, name: str) -> int | None:
    param = definition.params.get(name)
    value = None if param is None else param.value
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text(definition: RuleDefinition, name: str) -> str | None:
    param = definition.params.get(name)
    value = None if param is None else param.value
    return value if isinstance(value, str) else None


def _spelled(param: AnyParam) -> str:
    value = param.value
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _since(end: datetime, window: str) -> datetime:
    """Subtract a ``DURATION`` window from ``end``, months by the calendar.

    Returns:
        ``end`` minus the window, in UTC.

    """
    months, days, hours = window_parts(window) or (0, 0, 0)
    end = end.astimezone(UTC)
    year, month = divmod(end.year * _MONTHS_PER_YEAR + end.month - 1 - months, 12)
    month += 1
    day = min(end.day, calendar.monthrange(year, month)[1])
    return end.replace(year=year, month=month, day=day) - timedelta(
        days=days, hours=hours
    )


def _declaration_start(declaration: AggregateDeclaration, end: datetime) -> datetime:
    if declaration.window_kind is WindowKind.DURATION:
        return _since(end, declaration.window)
    return window_bounds(declaration, at=end)[0]


def _month_start(end: datetime) -> datetime:
    return bucket_start_for(BucketKind.MONTH, end)


def _amount_reaches(
    definition: RuleDefinition, txn: EnrichedTransaction, threshold: Decimal | None
) -> bool:
    unit_key = definition.unit_key
    amount = None if unit_key is None else txn.unit_amount(unit_key)
    return threshold is not None and amount is not None and amount >= threshold


def _holds(
    op: Literal["GTE", "GT", "LTE", "LT"], left: Decimal, right: Decimal
) -> bool:
    match op:
        case "GTE":
            return left >= right
        case "GT":
            return left > right
        case "LTE":
            return left <= right
        case "LT":
            return left < right


def _by_function(
    definition: RuleDefinition, function: AggregateFunction
) -> AggregateDeclaration | None:
    return next(
        (item for item in definition.aggregates if item.function is function), None
    )


def _threshold(
    definition: RuleDefinition, ctx: EvalContext, _reads: _Reads
) -> datetime | None:
    threshold = _decimal(definition, "threshold")
    if "threshold" in definition.params and not _amount_reaches(
        definition, ctx.txn, threshold
    ):
        return None
    return ctx.txn.occurred_at


def _aggregate(
    definition: RuleDefinition, ctx: EvalContext, reads: _Reads
) -> datetime | None:
    end = ctx.txn.occurred_at
    for condition in definition.conditions:
        left = reads.number(condition.aggregate)
        if condition.threshold is not None:
            right = _decimal(definition, condition.threshold)
        else:
            base = (
                None if condition.ratio_of is None else reads.number(condition.ratio_of)
            )
            # A ratio against an empty denominator never fires.
            right = (
                None
                if base is None or base == 0 or condition.ratio is None
                else condition.ratio * base
            )
        if left is None or right is None or not _holds(condition.op, left, right):
            return None
    return min(
        _declaration_start(declaration, end) for declaration, _ in reads.read.values()
    )


def _structuring(
    definition: RuleDefinition, ctx: EvalContext, reads: _Reads
) -> datetime | None:
    near_count = reads.value("near_count")
    near_sum = reads.value("near_sum")
    min_count = _integer(definition, "min_count")
    threshold = _decimal(definition, "threshold")
    window = _text(definition, "window")
    if (
        near_count is None
        or near_sum is None
        or min_count is None
        or threshold is None
        or window is None
    ):
        return None
    if near_count.count >= min_count and near_sum.value >= threshold:
        return _since(ctx.txn.occurred_at, window)
    return None


def _new_account_holds(definition: RuleDefinition, ctx: EvalContext) -> bool:
    days = _integer(definition, "new_account_days")
    if days is None:
        return True
    started = ctx.profile.relationship_started_at
    return started is not None and ctx.txn.occurred_at - started <= timedelta(days=days)


def _dormancy_holds(
    definition: RuleDefinition, ctx: EvalContext, reads: _Reads, start: datetime
) -> bool:
    days = _integer(definition, "dormant_days")
    if days is None:
        return True
    declaration = reads.declared(_LAST_SEEN)
    if declaration is None:
        return False
    # The last complete bucket before the rule's window, so the window's own
    # operations (the reactivation itself) never count as earlier activity.
    before = bucket_start_for(bucket_kind_for(declaration), start)
    seen = reads.value(_LAST_SEEN, at=before - timedelta(microseconds=1))
    if seen is None:
        return False
    if seen.last_at is None:
        return ctx.profile.history_months >= _DORMANT_HISTORY_MONTHS
    return start - seen.last_at > timedelta(days=days)


def _count_or_sum(
    definition: RuleDefinition,
    reads: _Reads,
    *,
    count_of: str | None,
    sum_of: str | None,
    total: Decimal | None,
) -> bool:
    count = _integer(definition, "count")
    counted = None if count_of is None or count is None else reads.value(count_of)
    summed = None if sum_of is None or total is None else reads.value(sum_of)
    by_count = counted is not None and count is not None and counted.count >= count
    by_sum = summed is not None and total is not None and summed.value >= total
    return by_count or by_sum


def _velocity(
    definition: RuleDefinition, ctx: EvalContext, reads: _Reads
) -> datetime | None:
    window = _text(definition, "window")
    if window is None or not _new_account_holds(definition, ctx):
        return None
    start = _since(ctx.txn.occurred_at, window)
    if not _dormancy_holds(definition, ctx, reads, start):
        return None
    counted = _by_function(definition, AggregateFunction.COUNT)
    summed = _by_function(definition, AggregateFunction.SUM)
    fired = _count_or_sum(
        definition,
        reads,
        count_of=None if counted is None else counted.name,
        sum_of=None if summed is None else summed.name,
        total=_decimal(definition, "sum"),
    )
    return start if fired else None


def _jurisdiction(
    definition: RuleDefinition, ctx: EvalContext, reads: _Reads
) -> datetime | None:
    txn = ctx.txn
    names = (_text(definition, "lists") or "").split(",")
    codes = ctx.lists.resolve_countries(name.strip() for name in names if name.strip())
    listed = txn.counterparty_country in codes or txn.geo_country in codes
    threshold = _decimal(definition, "threshold")
    if listed and _amount_reaches(definition, txn, threshold):
        return txn.occurred_at
    window = _text(definition, "window")
    if window is None:
        return None
    total = _decimal(definition, "sum")
    fired = _count_or_sum(
        definition,
        reads,
        count_of="to_listed_count",
        sum_of="to_listed_sum",
        total=threshold if total is None else total,
    )
    return _since(txn.occurred_at, window) if fired else None


def _against_declaration(
    definition: RuleDefinition, ctx: EvalContext, mtd: Decimal
) -> bool:
    declared = ctx.profile.declared_profile
    ratio = _decimal(definition, "ratio")
    if declared is None or ratio is None or declared.currency != ctx.txn.currency:
        return False
    monthly = (
        declared.monthly_inflow_minor
        if ctx.txn.direction is Direction.IN
        else declared.monthly_outflow_minor
    )
    return monthly is not None and monthly > 0 and mtd / monthly >= ratio


def _against_history(
    definition: RuleDefinition,
    ctx: EvalContext,
    reads: _Reads,
    *,
    month_to_date: Decimal,
    trailing_of: str,
) -> bool:
    months = ctx.profile.history_months
    history_ratio = _decimal(definition, "history_ratio")
    if months < _MIN_HISTORY_MONTHS or history_ratio is None:
        return False
    trailing = reads.value(trailing_of)
    if trailing is None:
        return False
    average = (trailing.value - month_to_date) / min(months, _TRAILING_MONTHS)
    return average > 0 and month_to_date / average >= history_ratio


def _profile(
    definition: RuleDefinition, ctx: EvalContext, reads: _Reads
) -> datetime | None:
    direction = ctx.txn.direction
    if direction not in {Direction.IN, Direction.OUT}:
        return None
    side = "in" if direction is Direction.IN else "out"
    mtd = reads.value(f"mtd_{side}")
    if mtd is None:
        return None
    fired = _against_declaration(definition, ctx, mtd.value) or _against_history(
        definition,
        ctx,
        reads,
        month_to_date=mtd.value,
        trailing_of=f"trailing_{side}",
    )
    return _month_start(ctx.txn.occurred_at) if fired else None


def _tier_limit(
    _definition: RuleDefinition, ctx: EvalContext, reads: _Reads
) -> datetime | None:
    tier = next(
        (item for item in ctx.policy.kyc.tiers if item.code == ctx.profile.tier), None
    )
    limit = None if tier is None else tier.limits.monthly_ops
    rule = ctx.policy.units.tier_limit_rule
    declaration = reads.declared("month_in")
    if limit is None or rule is None or declaration is None:
        return None
    # The template declares its unit statically; a snapshot that disagrees needs a
    # new template version, not a silent conversion (reconciliation finding 24).
    if f"{limit.value.unit}/{rule.value}" != declaration.unit_key:
        return None
    month_in = reads.value("month_in")
    # The sum already includes this transaction (§8.5); reaching the limit is
    # allowed, exceeding it is not (§12.2, reconciliation finding 26).
    if month_in is None or month_in.value <= limit.value.amount:
        return None
    return _month_start(ctx.txn.occurred_at)


type _Kind = Callable[[RuleDefinition, EvalContext, _Reads], datetime | None]

_KINDS: Mapping[RuleKind, _Kind] = {
    RuleKind.THRESHOLD: _threshold,
    RuleKind.AGGREGATE: _aggregate,
    RuleKind.STRUCTURING: _structuring,
    RuleKind.VELOCITY: _velocity,
    RuleKind.HIGH_RISK_JURISDICTION: _jurisdiction,
    RuleKind.PROFILE_DEVIATION: _profile,
    RuleKind.TIER_LIMIT: _tier_limit,
}


def _in_segment(definition: RuleDefinition, profile: EvalProfile) -> bool:
    segment = definition.segment
    if segment is None:
        return True
    pairs = (
        (segment.tier, profile.tier),
        (segment.risk_rating, profile.risk_rating),
        (segment.monitoring_profile, profile.monitoring_profile),
    )
    return all(
        not allowed or (value is not None and value in allowed)
        for allowed, value in pairs
    )


def _gates_hold(definition: RuleDefinition, profile: EvalProfile) -> bool:
    # The profile gates TxnFilter cannot express (plan 4b B2): the party's
    # screening status, and a PEP status other than the one named.
    status = _text(definition, "party_screening_status")
    pep_not = _text(definition, "pep_status_not")
    screened = status is None or profile.party_screening_status == status
    return screened and (pep_not is None or profile.pep_status != pep_not)


def applies(definition: RuleDefinition, ctx: EvalContext) -> bool:
    """Tell whether the rule can fire for this transaction at all.

    Returns:
        True for an ALERT rule whose ``on`` filter the transaction passes, whose
        key kind the transaction has a key for, and whose segment holds the
        customer (each non-empty list must contain the profile's value).

    """
    return (
        definition.family is RuleFamily.ALERT
        and matches(definition.on, ctx.txn)
        and ctx.txn.key_for(definition.key_kind) is not None
        and _in_segment(definition, ctx.profile)
    )


def _snapshot(
    declaration: AggregateDeclaration, value: AggregateValue
) -> AggregateSnapshot | None:
    number = _number(declaration, value)
    if number is None:
        return None
    unit = declaration.unit_key if declaration.measure is Measure.UNIT_AMOUNT else None
    return AggregateSnapshot(
        value=number, unit=unit, window=declaration.window, count=value.count
    )


def evaluate(definition: RuleDefinition, ctx: EvalContext) -> Firing | None:
    """Evaluate one rule for one transaction.

    The window ends at the transaction's ``occurred_at`` and starts at the
    kind's window before it (the transaction itself for an instant firing, the
    Mexico City month start for ``TIER_LIMIT`` and ``PROFILE_DEVIATION``).

    Returns:
        The firing, or None when the rule does not apply, its condition does not
        hold, or an aggregate it read is ``INCOMPLETE``.

    """
    key = ctx.txn.key_for(definition.key_kind)
    if key is None or not applies(definition, ctx):
        return None
    if not _gates_hold(definition, ctx.profile):
        return None
    reads = _Reads(definition, ctx.aggregates)
    start = _KINDS[definition.kind](definition, ctx, reads)
    if start is None or reads.incomplete:
        return None
    outcome = (
        HitOutcome(str(definition.params["outcome"].value))
        if definition.kind is RuleKind.TIER_LIMIT
        else HitOutcome.ALERT
    )
    snapshots = {
        name: snapshot
        for name, (declaration, value) in reads.read.items()
        if (snapshot := _snapshot(declaration, value)) is not None
    }
    return Firing(
        rule_code=definition.code,
        key_kind=definition.key_kind,
        key=key,
        window_start=start,
        window_end=ctx.txn.occurred_at,
        severity=definition.severity,
        outcome=outcome,
        aggregates=snapshots,
        params={name: _spelled(param) for name, param in definition.params.items()},
        explanation=definition.render("es-MX"),
    )
