"""The D19 lifecycle of a rule version (spec §9.4, SEC-501, FR-TM-011, FR-TM-016).

For one version, one user and that user's roles, ``evaluate_rule_actions`` tells
which actions are allowed and, for every other one, every reason it is blocked, in
the order ``STATE``, ``ROLE_REQUIRED``, ``NOT_AUTHOR``, ``SAME_AUTHOR``,
``SIMULATION_MISSING``, ``SIMULATION_STALE``, ``PARAM_OUT_OF_BOUNDS`` (plan 4b
Deviation 3). The same evaluation feeds the 409 of a refused transition and the
``allowed_actions`` and ``blocked_reasons`` of the staff view, as SP-03's
disposition matrix does.

Only the author submits, and only someone else approves: neither the author nor
a user who edited the version (four eyes; the database CHECK
``approver_not_author`` holds the author part too). Submission and approval need a
completed simulation of the version's current definition younger than
``MONITORING_SIMULATION_MAX_AGE_DAYS``. Activation starts a dated assignment,
retires the rule's current ``ACTIVE`` version and ends every assignment of the
rule still in force from that date (Deviation 5). Writes run in the caller's
transaction: rows, then audit, then ids-only events.

Every transition refuses a caller that is not a staff user (``ForbiddenError``),
answers ``NOT_FOUND`` for a version of another tenant, and raises
``RuleTransitionNotAllowedError`` with the matrix's reasons when it is blocked.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.postgres import ensure_declarations
from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.errors import RuleTransitionNotAllowedError
from xolotl.modules.monitoring.events import (
    RULE_VERSION_ACTIVATED,
    RULE_VERSION_RETIRED,
    publish_monitoring_event,
)
from xolotl.modules.monitoring.rules.assignments import (
    clear_rule_cache,
    load_rule_lists,
)
from xolotl.modules.monitoring.rules.definitions import (
    RuleDefinition,
    resolve_definition,
)
from xolotl.modules.monitoring.rules.enums import (
    RuleAction,
    RuleBlockedReason,
    RuleVersionStatus,
    SimulationStatus,
)
from xolotl.modules.monitoring.rules.models import (
    MonitoringRule,
    MonitoringRuleVersion,
    RuleAssignment,
    RuleSimulation,
    RuleTemplateVersion,
)
from xolotl.modules.monitoring.rules.templates import definition_of
from xolotl.modules.monitoring.rules.versions import WRITERS, check_params
from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.config import get_settings
from xolotl.platform.db import bound_context
from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import ForbiddenError, NotFoundError, ValidationFailedError
from xolotl.platform.jobs.queue import PostgresJobQueue
from xolotl.platform.tenancy.context import TenantContext

__all__ = [
    "APPROVERS",
    "WRITERS",
    "RuleActionSet",
    "activate_version",
    "approve_version",
    "editors_of",
    "evaluate_rule_actions",
    "reject_version",
    "retire_version",
    "submit_version",
    "version_editors",
    "version_out_of_bounds",
]

APPROVERS = frozenset({Role.MLRO, Role.ADMIN})
_REASON_MAX = 500
_NO_EDITORS: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class _Row:
    """One row of the spec §9.4 table."""

    states: frozenset[RuleVersionStatus]
    roles: frozenset[Role]
    # Only the author may act, or anyone but the author.
    author_only: bool = False
    not_author: bool = False
    # A fresh simulation of the current definition is required.
    simulation: bool = False


_EDITABLE = frozenset({RuleVersionStatus.DRAFT, RuleVersionStatus.SIMULATED})
_ROWS: Mapping[RuleAction, _Row] = {
    RuleAction.EDIT: _Row(_EDITABLE, WRITERS),
    RuleAction.SIMULATE: _Row(_EDITABLE, WRITERS),
    RuleAction.SUBMIT: _Row(
        frozenset({RuleVersionStatus.SIMULATED}),
        WRITERS,
        author_only=True,
        simulation=True,
    ),
    RuleAction.APPROVE: _Row(
        frozenset({RuleVersionStatus.IN_REVIEW}),
        APPROVERS,
        not_author=True,
        simulation=True,
    ),
    RuleAction.REJECT: _Row(frozenset({RuleVersionStatus.IN_REVIEW}), APPROVERS),
    RuleAction.ACTIVATE: _Row(frozenset({RuleVersionStatus.APPROVED}), APPROVERS),
    RuleAction.RETIRE: _Row(frozenset({RuleVersionStatus.ACTIVE}), APPROVERS),
}


@dataclass(frozen=True, slots=True)
class RuleActionSet:
    """The actions a user may take on a version, and why each other one is blocked."""

    allowed: tuple[RuleAction, ...]
    blocked: Mapping[RuleAction, tuple[RuleBlockedReason, ...]]


def _simulation_reason(
    version: MonitoringRuleVersion, simulation: RuleSimulation | None, now: datetime
) -> RuleBlockedReason | None:
    if (
        simulation is None
        or simulation.status is not SimulationStatus.COMPLETED
        or simulation.finished_at is None
    ):
        return RuleBlockedReason.SIMULATION_MISSING
    max_age = timedelta(days=get_settings().monitoring_simulation_max_age_days)
    if (
        now - simulation.finished_at >= max_age
        or simulation.definition_hash != version.definition_hash
    ):
        return RuleBlockedReason.SIMULATION_STALE
    return None


def evaluate_rule_actions(
    version: MonitoringRuleVersion,
    *,
    simulation: RuleSimulation | None,
    roles: frozenset[Role],
    user_id: UUID,
    now: datetime,
    out_of_bounds: bool = False,
    editors: frozenset[UUID] = _NO_EDITORS,
) -> RuleActionSet:
    """Evaluate every lifecycle action on a version for one user.

    ``simulation`` is the version's latest simulation, if any; ``out_of_bounds``
    says its parameters no longer fit its template's bounds, which blocks
    activation. ``editors`` are the users who edited the version (see
    ``version_editors``): they wrote part of it, so none of them may approve it.

    Returns:
        The allowed actions in ``RuleAction`` order, and the reasons of every
        blocked one.

    """
    simulation_reason = _simulation_reason(version, simulation, now)
    allowed: list[RuleAction] = []
    blocked: dict[RuleAction, tuple[RuleBlockedReason, ...]] = {}
    for action in RuleAction:
        row = _ROWS[action]
        reasons: list[RuleBlockedReason] = []
        if version.status not in row.states:
            reasons.append(RuleBlockedReason.STATE)
        if not roles & row.roles:
            reasons.append(RuleBlockedReason.ROLE_REQUIRED)
        if row.author_only and user_id != version.author_id:
            reasons.append(RuleBlockedReason.NOT_AUTHOR)
        if row.not_author and (user_id == version.author_id or user_id in editors):
            reasons.append(RuleBlockedReason.SAME_AUTHOR)
        if row.simulation and simulation_reason is not None:
            reasons.append(simulation_reason)
        if action is RuleAction.ACTIVATE and out_of_bounds:
            reasons.append(RuleBlockedReason.PARAM_OUT_OF_BOUNDS)
        if reasons:
            blocked[action] = tuple(reasons)
        else:
            allowed.append(action)
    return RuleActionSet(allowed=tuple(allowed), blocked=blocked)


def _staff(s: Session) -> TenantContext:
    ctx = bound_context(s)
    if ctx.actor_type is not ActorType.STAFF:
        msg = "only staff move a rule version through its lifecycle"
        raise ForbiddenError(msg)
    return ctx


def _version(
    s: Session, version_id: UUID, *, lock: bool = True
) -> MonitoringRuleVersion:
    query = select(MonitoringRuleVersion).where(MonitoringRuleVersion.id == version_id)
    if lock:
        query = query.with_for_update(key_share=True)
    version = s.scalars(query).one_or_none()
    if version is None:
        msg = "rule version not found"
        raise NotFoundError(msg)
    return version


def _locked_rule(
    s: Session, version_id: UUID
) -> tuple[MonitoringRuleVersion, list[MonitoringRuleVersion]]:
    """Lock a version's rule, then every version of it, and re-read them.

    The rule goes first, so activations and retirements of one rule queue on it
    instead of deadlocking on each other's version rows. ``FOR NO KEY UPDATE``
    still serialises the lifecycle writers (and ``create_version``'s ``FOR
    UPDATE``), but lets inserts whose foreign keys point at these rows proceed.

    Returns:
        The version and all versions of its rule, itself included.

    """
    version = _version(s, version_id, lock=False)
    _ = s.scalars(
        select(MonitoringRule)
        .where(MonitoringRule.id == version.rule_id)
        .with_for_update(key_share=True)
    ).one()
    siblings = list(
        s.scalars(
            select(MonitoringRuleVersion)
            .where(MonitoringRuleVersion.rule_id == version.rule_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    )
    return version, siblings


def editors_of(
    s: Session, version_ids: Collection[UUID]
) -> Mapping[UUID, frozenset[UUID]]:
    """Find, in one query, the staff users who edited each of some versions.

    Returns:
        By version id, the actors of its ``rules.version_updated`` audit rows; a
        version nobody edited is absent.

    """
    if not version_ids:
        return {}
    rows = s.execute(
        select(AuditEventRow.entity_id, AuditEventRow.actor_id).where(
            AuditEventRow.action == "rules.version_updated",
            AuditEventRow.entity_type == "monitoring_rule_version",
            AuditEventRow.entity_id.in_([str(item) for item in version_ids]),
            AuditEventRow.actor_type == ActorType.STAFF.value,
        )
    )
    editors: dict[UUID, set[UUID]] = {}
    for entity_id, actor_id in rows:
        editors.setdefault(UUID(str(entity_id)), set()).add(UUID(actor_id))
    return {version_id: frozenset(users) for version_id, users in editors.items()}


def version_editors(s: Session, version_id: UUID) -> frozenset[UUID]:
    """Find the staff users who edited a version after it was created.

    Returns:
        The actors of its ``rules.version_updated`` audit rows.

    """
    return editors_of(s, [version_id]).get(version_id, _NO_EDITORS)


def version_out_of_bounds(s: Session, version: MonitoringRuleVersion) -> bool:
    """Say whether a version's parameters left its template version's bounds.

    A custom rule is bounded by its own versions (plan 4b Deviation 17), so it
    never is. The staff view passes this to ``evaluate_rule_actions`` as
    activation does.

    Returns:
        True when ``check_params`` finds a violation.

    """
    if version.template_version_id is None:
        return False
    template = s.get_one(RuleTemplateVersion, version.template_version_id)
    definition = RuleDefinition.model_validate(version.definition)
    values = {name: param.value for name, param in definition.params.items()}
    source = f"template {template.template_code} v{template.version}"
    return bool(check_params(definition_of(template), values, source=source))


def _require(
    s: Session,
    version: MonitoringRuleVersion,
    action: RuleAction,
    ctx: TenantContext,
    *,
    out_of_bounds: bool = False,
) -> datetime:
    simulation = (
        None
        if version.simulation_id is None
        else s.get(RuleSimulation, (version.tenant_id, version.simulation_id))
    )
    now = clock.now()
    actions = evaluate_rule_actions(
        version,
        simulation=simulation,
        roles=ctx.roles,
        user_id=UUID(ctx.actor_id),
        now=now,
        out_of_bounds=out_of_bounds,
        editors=version_editors(s, version.id)
        if action is RuleAction.APPROVE
        else _NO_EDITORS,
    )
    if action not in actions.allowed:
        raise RuleTransitionNotAllowedError(actions.blocked[action])
    return now


def _audit(
    s: Session,
    action: str,
    version: MonitoringRuleVersion,
    detail: dict[str, JsonValue],
) -> None:
    _ = record(
        s,
        AuditEvent(
            action=action,
            entity_type="monitoring_rule_version",
            entity_id=str(version.id),
            detail=detail,
        ),
    )


def _publish(
    s: Session,
    event_type: str,
    version: MonitoringRuleVersion,
    assignment_id: UUID | None,
) -> None:
    _ = publish_monitoring_event(
        s,
        event_type,
        entity="monitoring_rule_version",
        entity_id=version.id,
        key=str(version.id),
        data={
            "rule_id": str(version.rule_id),
            "rule_version_id": str(version.id),
            "assignment_id": None if assignment_id is None else str(assignment_id),
        },
    )


def _simulation_detail(version: MonitoringRuleVersion) -> dict[str, JsonValue]:
    simulation_id = version.simulation_id
    return {"simulation_id": None if simulation_id is None else str(simulation_id)}


def submit_version(s: Session, version_id: UUID) -> MonitoringRuleVersion:
    """Send a simulated version to review, as its author.

    Returns:
        The version, ``IN_REVIEW``.

    """
    ctx = _staff(s)
    version = _version(s, version_id)
    now = _require(s, version, RuleAction.SUBMIT, ctx)
    version.status = RuleVersionStatus.IN_REVIEW
    version.submitted_at = now
    s.flush()
    _audit(s, "rules.version_submitted", version, _simulation_detail(version))
    return version


def approve_version(s: Session, version_id: UUID) -> MonitoringRuleVersion:
    """Approve a version in review, as an approver other than its author.

    Returns:
        The version, ``APPROVED``.

    """
    ctx = _staff(s)
    version = _version(s, version_id)
    now = _require(s, version, RuleAction.APPROVE, ctx)
    version.status = RuleVersionStatus.APPROVED
    version.approved_by = UUID(ctx.actor_id)
    version.approved_at = now
    s.flush()
    _audit(s, "rules.version_approved", version, _simulation_detail(version))
    return version


def reject_version(
    s: Session, version_id: UUID, *, reason: str
) -> MonitoringRuleVersion:
    """Send a version in review back to its author as a draft.

    Returns:
        The version, ``DRAFT``.

    Raises:
        ValidationFailedError: When the reason is empty or longer than 500
            characters once stripped.

    """
    ctx = _staff(s)
    text = reason.strip()
    if not 1 <= len(text) <= _REASON_MAX:
        msg = f"a rejection needs a reason of 1 to {_REASON_MAX} characters"
        raise ValidationFailedError(msg)
    version = _version(s, version_id)
    _ = _require(s, version, RuleAction.REJECT, ctx)
    version.status = RuleVersionStatus.DRAFT
    s.flush()
    # Codes and ids only: the reason's text is free and may name a person, and
    # 0013 has no encrypted column for it (plan 4b Deviation 18, Open question 12).
    _audit(
        s,
        "rules.version_rejected",
        version,
        {**_simulation_detail(version), "reason_given": True},
    )
    return version


def _assignment_of(s: Session, version_id: UUID) -> UUID | None:
    return s.scalars(
        select(RuleAssignment.id).where(RuleAssignment.rule_version_id == version_id)
    ).first()


def _end_assignments(
    s: Session, version_ids: list[UUID], *, from_day: date, last_day: date
) -> None:
    # Every assignment still in force on or after ``from_day`` ends on ``last_day``.
    rows = s.scalars(
        select(RuleAssignment).where(
            RuleAssignment.rule_version_id.in_(version_ids),
            or_(
                RuleAssignment.effective_to.is_(None),
                RuleAssignment.effective_to >= from_day,
            ),
        )
    )
    for row in rows:
        row.effective_to = last_day


def activate_version(
    s: Session,
    version_id: UUID,
    *,
    effective_from: date,
    product: str | None,
    queue: PostgresJobQueue,
) -> MonitoringRuleVersion:
    """Put an approved version in force from ``effective_from``.

    The rule's current ``ACTIVE`` version becomes ``RETIRED`` at once and every
    assignment of the rule still in force from ``effective_from`` ends the day
    before, so the rule keeps applying until its new version starts. The
    version's declarations are ensured, so their backfills start now.

    Returns:
        The version, ``ACTIVE``.

    Raises:
        ValidationFailedError: When ``effective_from`` is before today.

    """
    ctx = _staff(s)
    version, siblings = _locked_rule(s, version_id)
    now = _require(
        s,
        version,
        RuleAction.ACTIVATE,
        ctx,
        out_of_bounds=version_out_of_bounds(s, version),
    )
    if effective_from < policy_date(now):
        msg = "effective_from must be today or later"
        raise ValidationFailedError(msg)
    current = next(
        (
            v
            for v in siblings
            if v.status is RuleVersionStatus.ACTIVE and v.id != version.id
        ),
        None,
    )
    _end_assignments(
        s,
        [v.id for v in siblings],
        from_day=effective_from,
        last_day=effective_from - timedelta(days=1),
    )
    if current is not None:
        current.status = RuleVersionStatus.RETIRED
        current.retired_at = now
        current.retired_by_version_id = version.id
        # One ACTIVE version per rule: the retirement reaches the index first.
        s.flush()
    definition = RuleDefinition.model_validate(version.definition)
    assignment = RuleAssignment(
        tenant_id=version.tenant_id,
        rule_version_id=version.id,
        product=product,
        segment_filter=(
            None
            if definition.segment is None
            else definition.segment.model_dump(mode="json")
        ),
        effective_from=effective_from,
        approved_by=UUID(ctx.actor_id),
    )
    s.add(assignment)
    version.status = RuleVersionStatus.ACTIVE
    version.activated_at = now
    s.flush()
    resolved = resolve_definition(definition, load_rule_lists())
    _ = ensure_declarations(s, resolved.aggregates, queue=queue)
    clear_rule_cache()
    retired_assignment = None if current is None else _assignment_of(s, current.id)
    if current is not None:
        _audit(
            s,
            "rules.version_retired",
            current,
            {
                "assignment_id": None
                if retired_assignment is None
                else str(retired_assignment),
                "retired_by_version_id": str(version.id),
            },
        )
    _audit(
        s,
        "rules.version_activated",
        version,
        {
            "assignment_id": str(assignment.id),
            "effective_from": effective_from.isoformat(),
            "product": product,
            "category": "CHANGE_POLICY",
        },
    )
    if current is not None:
        _publish(s, RULE_VERSION_RETIRED, current, retired_assignment)
    _publish(s, RULE_VERSION_ACTIVATED, version, assignment.id)
    return version


def retire_version(s: Session, version_id: UUID) -> MonitoringRuleVersion:
    """Take an active version, and so its rule, out of force at the end of today.

    Every assignment of the rule still in force after today ends today, so a
    predecessor this version was to replace from a later date stops too.

    Returns:
        The version, ``RETIRED``.

    """
    ctx = _staff(s)
    version, siblings = _locked_rule(s, version_id)
    now = _require(s, version, RuleAction.RETIRE, ctx)
    today = policy_date(now)
    _end_assignments(
        s,
        [v.id for v in siblings],
        from_day=today + timedelta(days=1),
        last_day=today,
    )
    version.status = RuleVersionStatus.RETIRED
    version.retired_at = now
    s.flush()
    clear_rule_cache()
    assignment_id = _assignment_of(s, version.id)
    _audit(
        s,
        "rules.version_retired",
        version,
        {
            "assignment_id": None if assignment_id is None else str(assignment_id),
            "retired_by_version_id": None,
        },
    )
    _publish(s, RULE_VERSION_RETIRED, version, assignment_id)
    return version
