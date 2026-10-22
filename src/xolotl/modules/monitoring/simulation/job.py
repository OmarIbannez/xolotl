"""The simulation job: a rule version replayed over history, one row written (§13.1).

``request_simulation`` checks the version may be simulated (spec §9.4: a writer, a
``DRAFT`` or ``SIMULATED`` version) and the range (it ends today at the latest and
spans at most ``MONITORING_SIMULATION_MAX_DAYS`` days), writes a ``QUEUED``
``rule_simulation`` row and enqueues ``monitoring.simulate`` with one attempt.

``handle_simulate`` replays the tenant's processed rows of the range through the
version alone, in memory (``replay_pages``), and commits its tallies and cursor
after each page of whole parties, so a run that dies resumes at the next party
with exact counts (plan 4b Deviation 12). It writes nothing but its own row,
the version's ``SIMULATED`` status when the version still has the definition
that was replayed, and ``monitoring.simulation_completed.v1`` (PRV-503). A
failure is final (Deviation 16): the row becomes ``FAILED`` with the exception's
class (and code) only, the event says so, and the job is not retried.

The overlap with real outcomes reads the hits already stored for the same
parties and, through a reader the composition root installs (monitoring may not
import the screening engine), when a party was last screened if its status is
``TRUE_MATCH``.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.errors import (
    RuleTransitionNotAllowedError,
    SimulationRangeTooLongError,
)
from xolotl.modules.monitoring.events import (
    SIMULATION_COMPLETED,
    publish_monitoring_event,
)
from xolotl.modules.monitoring.rules.assignments import (
    LoadedRule,
    RuleSet,
    load_rule_lists,
)
from xolotl.modules.monitoring.rules.definitions import (
    RuleDefinition,
    resolve_definition,
)
from xolotl.modules.monitoring.rules.enums import (
    RuleAction,
    RuleVersionStatus,
    SimulationStatus,
)
from xolotl.modules.monitoring.rules.lifecycle import evaluate_rule_actions
from xolotl.modules.monitoring.rules.models import (
    MonitoringRule,
    MonitoringRuleVersion,
    RuleHit,
    RuleSimulation,
)
from xolotl.modules.monitoring.simulation.engine import (
    ReplayPage,
    SimulatedHit,
    replay_pages,
)
from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.config import get_settings
from xolotl.platform.db import bound_context
from xolotl.platform.enums import ActorType
from xolotl.platform.errors import (
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
    XolotlError,
)
from xolotl.platform.jobs.queue import Job, PostgresJobQueue

SIMULATE_KIND = "monitoring.simulate"
SAMPLE_LIMIT = 50
# A simulation is not tied to an assignment: its rule applies to every product.
_NO_ASSIGNMENT = UUID(int=0)
_EDITABLE = frozenset({RuleVersionStatus.DRAFT, RuleVersionStatus.SIMULATED})
_FINISHED = frozenset({SimulationStatus.COMPLETED, SimulationStatus.FAILED})

# When a party whose screening status is TRUE_MATCH was last screened, else None.
type TrueMatchReader = Callable[[Session, UUID], datetime | None]

_true_match: list[TrueMatchReader] = []


def use_true_match_reader(reader: TrueMatchReader | None) -> None:
    """Install the reader of a party's true-match screening; None removes it."""
    _true_match.clear()
    if reader is not None:
        _true_match.append(reader)


def _staff_id(s: Session) -> UUID:
    ctx = bound_context(s)
    if ctx.actor_type is not ActorType.STAFF:
        msg = "only staff simulate a rule version"
        raise ForbiddenError(msg)
    return UUID(ctx.actor_id)


def _check_range(range_start: date, range_end: date, *, today: date) -> None:
    if range_start > range_end:
        msg = "range_start must not be after range_end"
        raise ValidationFailedError(msg)
    if range_end > today:
        msg = "range_end must be today or earlier"
        raise ValidationFailedError(msg)
    most = get_settings().monitoring_simulation_max_days
    if (range_end - range_start).days + 1 > most:
        msg = f"a simulation spans at most {most} days"
        raise SimulationRangeTooLongError(msg)


def _create(
    s: Session, version_id: UUID, *, range_start: date, range_end: date
) -> RuleSimulation:
    user_id = _staff_id(s)
    ctx = bound_context(s)
    version = s.scalars(
        select(MonitoringRuleVersion).where(MonitoringRuleVersion.id == version_id)
    ).one_or_none()
    if version is None:
        msg = "rule version not found"
        raise NotFoundError(msg)
    now = clock.now()
    actions = evaluate_rule_actions(
        version, simulation=None, roles=ctx.roles, user_id=user_id, now=now
    )
    if RuleAction.SIMULATE not in actions.allowed:
        raise RuleTransitionNotAllowedError(actions.blocked[RuleAction.SIMULATE])
    _check_range(range_start, range_end, today=policy_date(now))
    simulation = RuleSimulation(
        tenant_id=version.tenant_id,
        rule_version_id=version.id,
        requested_by=user_id,
        range_start=range_start,
        range_end=range_end,
        status=SimulationStatus.QUEUED,
        definition_hash=version.definition_hash,
    )
    s.add(simulation)
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="rules.simulation_requested",
            entity_type="monitoring_rule_version",
            entity_id=str(version.id),
            detail={
                "simulation_id": str(simulation.id),
                "range_start": range_start.isoformat(),
                "range_end": range_end.isoformat(),
            },
        ),
    )
    return simulation


def request_simulation(
    s: Session,
    version_id: UUID,
    *,
    range_start: date,
    range_end: date,
    queue: PostgresJobQueue,
) -> RuleSimulation:
    """Queue a simulation of a version over a range of Mexico City dates.

    A caller who is not staff is refused (``FORBIDDEN``), a version of another
    tenant is ``NOT_FOUND``, a caller who may not simulate it now gets
    ``RULE_TRANSITION_NOT_ALLOWED`` (``STATE``, ``ROLE_REQUIRED``), a range that
    starts after it ends or ends after today ``VALIDATION_FAILED``, and one longer
    than ``MONITORING_SIMULATION_MAX_DAYS`` days ``SIMULATION_RANGE_TOO_LONG``.

    Returns:
        The ``QUEUED`` simulation; its job runs once.

    """
    simulation = _create(s, version_id, range_start=range_start, range_end=range_end)
    _ = queue.enqueue(
        s,
        kind=SIMULATE_KIND,
        payload={"simulation_id": str(simulation.id)},
        key=f"simulate:{simulation.id}",
        max_attempts=1,
    )
    return simulation


def _version_rule(s: Session, version: MonitoringRuleVersion) -> RuleSet:
    rule = s.get_one(MonitoringRule, (version.tenant_id, version.rule_id))
    lists = load_rule_lists()
    definition = RuleDefinition.model_validate(version.definition)
    loaded = LoadedRule(
        rule_id=version.rule_id,
        rule_version_id=version.id,
        rule_code=rule.code,
        assignment_id=_NO_ASSIGNMENT,
        definition=resolve_definition(definition, lists),
        segment=definition.segment,
        sync=version.sync,
        definition_hash=version.definition_hash,
        typology=tuple(definition.typology),
    )
    return RuleSet(
        on=policy_date(clock.now()), product=None, rules=(loaded,), lists=lists
    )


def _week(at: datetime) -> str:
    year, week, _ = policy_date(at).isocalendar()
    return f"{year}-W{week:02d}"


def _sample_entry(hit: SimulatedHit) -> dict[str, JsonValue]:
    aggregates: dict[str, JsonValue] = {
        name: {
            "value": str(snapshot.value),
            "unit": snapshot.unit,
            "window": snapshot.window,
        }
        for name, snapshot in hit.aggregates.items()
    }
    return {
        "rule_version_id": str(hit.rule_version_id),
        "party_id": str(hit.party_id),
        "relationship_id": str(hit.relationship_id),
        "trigger_transaction_id": str(hit.trigger_transaction_id),
        "dedup_key": hit.dedup_key,
        "window_start": hit.window_start.isoformat(),
        "window_end": hit.window_end.isoformat(),
        "severity": hit.severity.value,
        "outcome": hit.outcome.value,
        "aggregates": aggregates,
    }


def _int(raw: JsonValue) -> int:
    return raw if isinstance(raw, int) else 0


@dataclass(slots=True)
class _Tallies:
    """What the row accumulates page after page; every party is in one page."""

    transactions_read: int
    hit_count: int
    customers_hit: int
    weeks: dict[str, list[int]]
    overlap: dict[str, int]
    sample: list[JsonValue] = field(default_factory=list[JsonValue])

    @classmethod
    def of(cls, simulation: RuleSimulation) -> "_Tallies":
        weeks: dict[str, list[int]] = {}
        for item in simulation.hits_per_week or []:
            if isinstance(item, dict):
                weeks[str(item["week"])] = [_int(item["hits"]), _int(item["customers"])]
        stored = simulation.overlap or {}
        return cls(
            transactions_read=simulation.transactions_read,
            hit_count=simulation.hit_count,
            customers_hit=simulation.customers_hit,
            weeks=weeks,
            overlap={
                name: _int(stored.get(name, 0))
                for name in ("existing_hits", "same_customers", "later_true_match")
            },
            sample=list(simulation.sample or []),
        )

    def add(self, s: Session, page: ReplayPage) -> None:
        self.transactions_read += page.transactions_read
        self.hit_count += len(page.hits)
        by_party: dict[UUID, list[SimulatedHit]] = defaultdict(list)
        for hit in page.hits:
            by_party[hit.party_id].append(hit)
        self.customers_hit += len(by_party)
        per_week: dict[str, set[UUID]] = defaultdict(set)
        for hit in page.hits:
            week = _week(hit.window_end)
            per_week[week].add(hit.party_id)
            self.weeks.setdefault(week, [0, 0])[0] += 1
        for week, parties in per_week.items():
            self.weeks[week][1] += len(parties)
        self._overlap(s, by_party)
        room = SAMPLE_LIMIT - len(self.sample)
        self.sample += [_sample_entry(hit) for hit in page.hits[: max(room, 0)]]

    def _overlap(self, s: Session, by_party: dict[UUID, list[SimulatedHit]]) -> None:
        if not by_party:
            return
        stored = s.execute(
            select(
                RuleHit.id, RuleHit.party_id, RuleHit.window_start, RuleHit.window_end
            ).where(RuleHit.party_id.in_(by_party))
        ).all()
        overlapping: set[UUID] = set()
        parties: set[UUID] = set()
        for hit_id, party_id, start, end in stored:
            if any(
                start <= simulated.window_end and end >= simulated.window_start
                for simulated in by_party[party_id]
            ):
                overlapping.add(hit_id)
                parties.add(party_id)
        self.overlap["existing_hits"] += len(overlapping)
        self.overlap["same_customers"] += len(parties)
        self.overlap["later_true_match"] += _later_true_matches(s, by_party)

    def write(self, simulation: RuleSimulation) -> None:
        simulation.transactions_read = self.transactions_read
        simulation.hit_count = self.hit_count
        simulation.customers_hit = self.customers_hit
        simulation.hits_per_week = [
            {"week": week, "hits": hits, "customers": customers}
            for week, (hits, customers) in sorted(self.weeks.items())
        ]
        simulation.overlap = dict[str, JsonValue](self.overlap)
        simulation.sample = list(self.sample)


def _later_true_matches(s: Session, by_party: dict[UUID, list[SimulatedHit]]) -> int:
    if not _true_match:
        return 0
    reader = _true_match[0]
    later = 0
    for party_id, found in by_party.items():
        matched_at = reader(s, party_id)
        last_end = max(hit.window_end for hit in found)
        if matched_at is not None and matched_at > last_end:
            later += 1
    return later


def _error_summary(exc: BaseException) -> str:
    # The class and the code only: an exception's message may carry data.
    name = type(exc).__name__
    return f"{name}: {exc.code}" if isinstance(exc, XolotlError) else name


def _publish(s: Session, simulation: RuleSimulation) -> None:
    _ = publish_monitoring_event(
        s,
        SIMULATION_COMPLETED,
        entity="rule_simulation",
        entity_id=simulation.id,
        key=str(simulation.rule_version_id),
        data={
            "simulation_id": str(simulation.id),
            "rule_version_id": str(simulation.rule_version_id),
            "status": simulation.status.value,
        },
    )


def _mark_version(s: Session, simulation: RuleSimulation) -> None:
    # The lifecycle's lock order: the rule, then the version.
    version = s.get_one(
        MonitoringRuleVersion, (simulation.tenant_id, simulation.rule_version_id)
    )
    _ = s.scalars(
        select(MonitoringRule)
        .where(MonitoringRule.id == version.rule_id)
        .with_for_update(key_share=True)
    ).one()
    version = s.scalars(
        select(MonitoringRuleVersion)
        .where(MonitoringRuleVersion.id == simulation.rule_version_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    ).one()
    if (
        version.status in _EDITABLE
        and version.definition_hash == simulation.definition_hash
    ):
        version.status = RuleVersionStatus.SIMULATED
        version.simulation_id = simulation.id


def _replay(s: Session, simulation: RuleSimulation) -> None:
    version = s.get_one(
        MonitoringRuleVersion, (simulation.tenant_id, simulation.rule_version_id)
    )
    if simulation.status is SimulationStatus.QUEUED:
        # The hash of the definition actually replayed: an edit after the request
        # leaves the version unmarked at the end.
        simulation.status = SimulationStatus.RUNNING
        simulation.started_at = clock.now()
        simulation.definition_hash = version.definition_hash
        s.commit()
    elif version.definition_hash != simulation.definition_hash:
        msg = "the version changed while its simulation was interrupted"
        raise ValidationFailedError(msg)
    ruleset = _version_rule(s, version)
    tallies = _Tallies.of(simulation)
    after = None if simulation.cursor is None else UUID(simulation.cursor)
    for page in replay_pages(
        s,
        ruleset,
        range_start=simulation.range_start,
        range_end=simulation.range_end,
        page=get_settings().monitoring_agg_backfill_batch,
        after=after,
    ):
        tallies.add(s, page)
        tallies.write(simulation)
        simulation.cursor = str(page.last_party_id)
        s.commit()
    _mark_version(s, simulation)
    simulation.status = SimulationStatus.COMPLETED
    simulation.finished_at = clock.now()
    s.flush()
    _publish(s, simulation)


def _simulate(
    s: Session, tenant_id: UUID, simulation_id: UUID
) -> RuleSimulation | None:
    simulation = s.get(RuleSimulation, (tenant_id, simulation_id))
    if simulation is None or simulation.status in _FINISHED:
        return simulation
    try:
        _replay(s, simulation)
    except Exception as exc:
        s.rollback()
        failed = s.get_one(RuleSimulation, (tenant_id, simulation_id))
        failed.status = SimulationStatus.FAILED
        failed.finished_at = clock.now()
        failed.last_error = _error_summary(exc)
        s.flush()
        _publish(s, failed)
        s.commit()
        raise
    return simulation


def handle_simulate(s: Session, job: Job) -> None:
    """Run the simulation a ``monitoring.simulate`` job names.

    A simulation already completed or failed is left as it is; a ``RUNNING`` one
    resumes after its cursor.
    """
    raw = job.payload.get("simulation_id")
    _ = _simulate(s, job.tenant_id, UUID(str(raw)))


def run_simulation_now(
    s: Session, version_id: UUID, *, range_start: date, range_end: date
) -> RuleSimulation:
    """Request and run a simulation in this session, without the queue.

    The CLI and ``rules activate`` use it; the session commits as it goes.

    Returns:
        The finished simulation.

    """
    simulation = _create(s, version_id, range_start=range_start, range_end=range_end)
    tenant_id, simulation_id = simulation.tenant_id, simulation.id
    s.commit()
    _ = _simulate(s, tenant_id, simulation_id)
    return s.get_one(RuleSimulation, (tenant_id, simulation_id))
