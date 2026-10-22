"""The dry-run replay of a range against the stored hits (spec §13.1, FR-TM-018).

``replay_range`` runs the rules in force on the range's last day through B8's
in-memory engine (``replay_rule_set``) over the range's ``PROCESSED`` rows and
compares the ``(rule_version_id, dedup_key)`` pairs it produces with the
``rule_hit`` rows of the same versions whose ``first_occurred_at`` falls in the
range. A stored hit the replay does not produce is ``missing``; a simulated hit
the store does not hold is ``extra``. The range is identical when no compared rule
has either.

Processing evaluates a transaction under the tenant-wide rules plus its own
product's, so the replay runs the tenant-wide rules over every row and each
product's own rules over that product's rows only.

What the comparison cannot see, by construction:

- Windows start cold at the range's start: an aggregate that needs rows from
  before the range sees fewer of them than processing did, so a hit near the
  start may be ``missing`` (and a cooldown that began before the range does not
  suppress a hit, which may then be ``extra``). Start the range a window early.
- The rules are those in force on the last day. A version activated during the
  range is replayed over the whole range (its earlier hits are ``extra``), and a
  version whose assignment ended before the last day is not replayed and its
  stored hits are not read.
- A rule keyed on ``COUNTERPARTY`` or ``DEVICE`` (its key, dedup key or a
  declaration) is listed with its stored count and not compared: the engine
  replays one party at a time (plan 4b Deviation 12), so a key several parties
  share would diverge from processing.

The replay writes nothing: the caller's transaction is made read only first, so
a write anywhere on the way would be an error rather than a side effect.
"""

from dataclasses import dataclass, replace
from datetime import date
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.errors import SimulationRangeTooLongError
from xolotl.modules.monitoring.rules.assignments import (
    LoadedRule,
    RuleSet,
    active_rules,
    assignments_in_force,
)
from xolotl.modules.monitoring.rules.models import MonitoringRuleVersion, RuleHit
from xolotl.modules.monitoring.simulation.engine import range_bounds, replay_rule_set
from xolotl.modules.monitoring.simulation.job import cross_party_keys
from xolotl.platform.config import get_settings
from xolotl.platform.errors import ValidationFailedError

type _HitKey = tuple[UUID, str]


@dataclass(frozen=True, slots=True)
class RuleReplay:
    """One rule version's replay; ``compared`` is false for a cross-party key."""

    rule_code: str
    rule_version_id: UUID
    version: int
    simulated: int
    stored: int
    missing: int
    extra: int
    compared: bool


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """The replay of a range of Mexico City days, both included."""

    range_start: date
    range_end: date
    rules: tuple[RuleReplay, ...]
    identical: bool


def _check_range(range_start: date, range_end: date) -> None:
    if range_start > range_end:
        msg = "range_start must not be after range_end"
        raise ValidationFailedError(msg)
    most = get_settings().monitoring_simulation_max_days
    if (range_end - range_start).days + 1 > most:
        msg = f"a replay spans at most {most} days"
        raise SimulationRangeTooLongError(msg)


def _passes(s: Session, *, on: date) -> list[tuple[str | None, RuleSet]]:
    # The tenant-wide rules over every row, then each product's own rules.
    tenant_wide = active_rules(s, on=on, product=None)
    passes: list[tuple[str | None, RuleSet]] = [(None, tenant_wide)]
    everywhere = {loaded.rule_version_id for loaded in tenant_wide.rules}
    products = sorted({
        assignment.product
        for assignment, _ in assignments_in_force(s, on=on)
        if assignment.product is not None
    })
    for product in products:
        ruleset = active_rules(s, on=on, product=product)
        own = tuple(
            loaded
            for loaded in ruleset.rules
            if loaded.rule_version_id not in everywhere
        )
        passes.append((product, replace(ruleset, rules=own)))
    return passes


def _stored(
    s: Session, versions: set[UUID], *, range_start: date, range_end: date
) -> set[_HitKey]:
    if not versions:
        return set()
    start, end = range_bounds(range_start, range_end)
    rows = s.execute(
        select(RuleHit.rule_version_id, RuleHit.dedup_key).where(
            RuleHit.rule_version_id.in_(versions),
            RuleHit.first_occurred_at >= start,
            RuleHit.first_occurred_at < end,
        )
    )
    return {(version_id, dedup_key) for version_id, dedup_key in rows}


def _numbers(s: Session, versions: set[UUID]) -> dict[UUID, int]:
    if not versions:
        return {}
    query = select(MonitoringRuleVersion.id, MonitoringRuleVersion.version).where(
        MonitoringRuleVersion.id.in_(versions)
    )
    return dict(s.execute(query).all())


def replay_range(s: Session, *, range_start: date, range_end: date) -> ReplayReport:
    """Replay a range of Mexico City days and compare it with the stored hits.

    A range that starts after it ends is ``VALIDATION_FAILED``; one longer than
    ``MONITORING_SIMULATION_MAX_DAYS`` days is ``SIMULATION_RANGE_TOO_LONG``.

    Returns:
        One entry per rule version in force on ``range_end``, by rule code and
        version, and whether every compared version reproduced its hits.

    """
    _check_range(range_start, range_end)
    _ = s.execute(text("SET TRANSACTION READ ONLY"))
    page = get_settings().monitoring_agg_backfill_batch
    rules: dict[UUID, LoadedRule] = {}
    compared: set[UUID] = set()
    simulated: set[_HitKey] = set()
    for product, ruleset in _passes(s, on=range_end):
        replayed = tuple(
            loaded
            for loaded in ruleset.rules
            if not cross_party_keys(loaded.definition)
        )
        rules.update({loaded.rule_version_id: loaded for loaded in ruleset.rules})
        compared.update(loaded.rule_version_id for loaded in replayed)
        if not replayed:
            continue
        hits = replay_rule_set(
            s,
            replace(ruleset, rules=replayed),
            range_start=range_start,
            range_end=range_end,
            page=page,
            product=product,
        )
        simulated.update((hit.rule_version_id, hit.dedup_key) for hit in hits)
    versions = set(rules)
    stored = _stored(s, versions, range_start=range_start, range_end=range_end)
    numbers = _numbers(s, versions)
    entries: list[RuleReplay] = []
    for version_id, loaded in rules.items():
        mine = {key for key in simulated if key[0] == version_id}
        kept = {key for key in stored if key[0] == version_id}
        entries.append(
            RuleReplay(
                rule_code=loaded.rule_code,
                rule_version_id=version_id,
                version=numbers[version_id],
                simulated=len(mine),
                stored=len(kept),
                missing=len(kept - mine) if version_id in compared else 0,
                extra=len(mine - kept),
                compared=version_id in compared,
            )
        )
    entries.sort(key=lambda entry: (entry.rule_code, entry.version))
    return ReplayReport(
        range_start=range_start,
        range_end=range_end,
        rules=tuple(entries),
        identical=all(entry.missing == entry.extra == 0 for entry in entries),
    )
