"""Which rule versions apply on a date and to a product (spec §9.5, Decision 13).

A ``rule_assignment`` applies one version from ``effective_from`` to
``effective_to`` (open when null), to one product or, with no product, to every
product, as ``tenant_pack_assignment`` does. Assignments resolve by date and
versions by status: a version still applies while it is ``ACTIVE`` or ``RETIRED``
and its assignment is in force, so a rule keeps applying until its successor
starts (plan 4b Deviation 5).

``active_rules`` loads the versions in force with their list names resolved
(reconciliation finding 3) and caches the set per process for
``MONITORING_RULE_CACHE_SECONDS``. The key carries the tenant's activation and
retirement stamps, so another process's activation is seen on the next read, and
this process's activations clear the cache at once.
"""

import hashlib
import threading
from dataclasses import dataclass
from datetime import date
from time import monotonic
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import AggregateDeclaration
from xolotl.modules.monitoring.rules.definitions import (
    RuleDefinition,
    SegmentFilter,
    resolve_definition,
)
from xolotl.modules.monitoring.rules.enums import RuleVersionStatus
from xolotl.modules.monitoring.rules.models import (
    MonitoringRule,
    MonitoringRuleVersion,
    RuleAssignment,
)
from xolotl.platform.config import get_settings
from xolotl.platform.db import bound_context
from xolotl.platform.policy import load_jurisdictions
from xolotl.platform.policy.jurisdictions import JURISDICTIONS_FILE, JurisdictionLists

# The statuses whose assignments still apply (Deviation 5).
_APPLYING = (RuleVersionStatus.ACTIVE, RuleVersionStatus.RETIRED)


@dataclass(frozen=True, slots=True)
class LoadedRule:
    """A rule version in force, ready for the evaluator.

    ``definition`` has its list names resolved and ``lists_hash`` set;
    ``definition_hash`` is the stored version's hash, which every hit records.
    """

    rule_id: UUID
    rule_version_id: UUID
    rule_code: str
    assignment_id: UUID
    definition: RuleDefinition
    segment: SegmentFilter | None
    sync: bool
    definition_hash: bytes
    typology: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuleSet:
    """The rules in force for one tenant, date and product, by rule code."""

    on: date
    product: str | None
    rules: tuple[LoadedRule, ...]
    lists: JurisdictionLists

    @property
    def declarations(self) -> tuple[AggregateDeclaration, ...]:
        """Union the rules' declarations, one per ``agg_id``, in rule order.

        Returns:
            The resolved declarations, deduplicated.

        """
        seen: dict[str, AggregateDeclaration] = {}
        for loaded in self.rules:
            for declaration in loaded.definition.aggregates:
                _ = seen.setdefault(declaration.agg_id(), declaration)
        return tuple(seen.values())

    @property
    def sync_rules(self) -> tuple[LoadedRule, ...]:
        """The rules the decide path evaluates too (spec §12)."""
        return tuple(loaded for loaded in self.rules if loaded.sync)

    def ruleset_hash(self) -> bytes:
        """Hash the sync rules' definition hashes, whatever their order.

        Returns:
            The SHA-256 of the sorted hashes (``txn_decision.ruleset_hash``).

        """
        hashes = sorted(loaded.definition_hash for loaded in self.sync_rules)
        return hashlib.sha256(b"".join(hashes)).digest()


def _dated(on: date) -> ColumnElement[bool]:
    # In force on ``on``, for whichever product.
    return (
        (RuleAssignment.effective_from <= on)
        & or_(RuleAssignment.effective_to.is_(None), RuleAssignment.effective_to >= on)
        & MonitoringRuleVersion.status.in_(_APPLYING)
    )


def _in_force(on: date, product: str | None) -> ColumnElement[bool]:
    applies = RuleAssignment.product.is_(None)
    if product is not None:
        applies = or_(applies, RuleAssignment.product == product)
    return _dated(on) & applies


def _version_of_assignment() -> ColumnElement[bool]:
    return (MonitoringRuleVersion.tenant_id == RuleAssignment.tenant_id) & (
        MonitoringRuleVersion.id == RuleAssignment.rule_version_id
    )


def _rule_of_version() -> ColumnElement[bool]:
    return (MonitoringRule.tenant_id == MonitoringRuleVersion.tenant_id) & (
        MonitoringRule.id == MonitoringRuleVersion.rule_id
    )


def assignments_on(
    s: Session, *, on: date, product: str | None
) -> list[RuleAssignment]:
    """List the assignments in force on ``on`` that apply to ``product``.

    Returns:
        The tenant-wide rows plus, when ``product`` is given, that product's rows,
        of versions ``ACTIVE`` or ``RETIRED``, by start date.

    """
    query = (
        select(RuleAssignment)
        .join(MonitoringRuleVersion, _version_of_assignment())
        .where(_in_force(on, product))
    )
    return list(s.scalars(query.order_by(RuleAssignment.effective_from)))


def assignments_in_force(s: Session, *, on: date) -> list[tuple[RuleAssignment, str]]:
    """List every assignment in force on ``on``, whatever its product.

    Returns:
        Each assignment with its rule code, by start date and rule code, of
        versions ``ACTIVE`` or ``RETIRED``.

    """
    query = (
        select(RuleAssignment, MonitoringRule.code)
        .join(MonitoringRuleVersion, _version_of_assignment())
        .join(MonitoringRule, _rule_of_version())
        .where(_dated(on))
        .order_by(RuleAssignment.effective_from, MonitoringRule.code)
    )
    return [(row, code) for row, code in s.execute(query)]


def _segment(raw: object) -> SegmentFilter | None:
    return None if raw is None else SegmentFilter.model_validate(raw)


def load_rule_lists() -> JurisdictionLists:
    """Read the jurisdiction lists rule definitions name, from ``packs_dir``.

    Returns:
        The named country and region sets with the file's hash.

    """
    return load_jurisdictions(get_settings().packs_dir / JURISDICTIONS_FILE.name)


def _load(s: Session, *, on: date, product: str | None) -> RuleSet:
    lists = load_rule_lists()
    query = (
        select(RuleAssignment, MonitoringRuleVersion, MonitoringRule.code)
        .join(MonitoringRuleVersion, _version_of_assignment())
        .join(MonitoringRule, _rule_of_version())
        .where(_in_force(on, product))
        .order_by(MonitoringRule.code, MonitoringRuleVersion.version)
    )
    rules: list[LoadedRule] = []
    for assignment, version, code in s.execute(query):
        definition = RuleDefinition.model_validate(version.definition)
        rules.append(
            LoadedRule(
                rule_id=version.rule_id,
                rule_version_id=version.id,
                rule_code=code,
                assignment_id=assignment.id,
                definition=resolve_definition(definition, lists),
                segment=_segment(assignment.segment_filter),
                sync=version.sync,
                definition_hash=version.definition_hash,
                typology=tuple(definition.typology),
            )
        )
    return RuleSet(on=on, product=product, rules=tuple(rules), lists=lists)


type _Key = tuple[UUID, date, str | None, tuple[object, ...]]
_cache: dict[_Key, tuple[float, RuleSet]] = {}
# The worker's job threads and the API's request threads share the cache: every
# read, sweep and write of the dict holds this lock (never the rule set's load).
_cache_lock = threading.Lock()


def _stamp(s: Session) -> tuple[object, ...]:
    # What any activation or retirement of the tenant changes, even two at once.
    version = MonitoringRuleVersion
    row = s.execute(
        select(
            func.count(version.activated_at),
            func.count(version.retired_at),
            func.max(version.activated_at),
            func.max(version.retired_at),
        )
    ).one()
    return tuple(row)


def active_rules(s: Session, *, on: date, product: str | None) -> RuleSet:
    """Load the tenant's rules in force on ``on`` for ``product``.

    Returns:
        The rule set, from this process's cache while it is fresh.

    """
    ttl = get_settings().monitoring_rule_cache_seconds
    if ttl == 0:
        return _load(s, on=on, product=product)
    key: _Key = (bound_context(s).require_tenant_id(), on, product, _stamp(s))
    now = monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        for stale in [k for k, (expires, _) in _cache.items() if expires <= now]:
            del _cache[stale]
    ruleset = _load(s, on=on, product=product)
    with _cache_lock:
        _cache[key] = (now + ttl, ruleset)
    return ruleset


def clear_rule_cache() -> None:
    """Forget every cached rule set of this process."""
    with _cache_lock:
        _cache.clear()
