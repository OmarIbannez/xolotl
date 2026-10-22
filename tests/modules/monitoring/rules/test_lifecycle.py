"""The D19 rule lifecycle: the matrix of spec §9.4 and its transitions (plan 4b B6).

The oracle below is written by hand from the spec table, not from the
implementation: every state x role x same-or-other user x simulation x action is
checked, and the blocked reasons must come in the documented order (plan 4b
Deviation 3).
"""

import itertools
from datetime import date, timedelta
from functools import partial
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.helpers import audit_events, new_tenant, staff_session, system_ctx
from tests.modules.monitoring.rules.lifecycle_support import (
    APPROVER_ROLES,
    AUTHOR_ROLES,
    NOW,
    TODAY,
    activated,
    add_staff,
    approved,
    draft,
    freeze,
    load_library,
    simulate,
    submitted,
)
from xolotl.modules.monitoring.aggregates.models import AggDeclaration
from xolotl.modules.monitoring.errors import RuleTransitionNotAllowedError
from xolotl.modules.monitoring.events import (
    RULE_VERSION_ACTIVATED,
    RULE_VERSION_RETIRED,
)
from xolotl.modules.monitoring.kinds import AGG_BACKFILL_KIND
from xolotl.modules.monitoring.rules import lifecycle
from xolotl.modules.monitoring.rules.assignments import active_rules
from xolotl.modules.monitoring.rules.definitions import (
    RuleDefinition,
    SegmentFilter,
    resolve_definition,
)
from xolotl.modules.monitoring.rules.enums import (
    RuleAction,
    RuleBlockedReason,
    RuleVersionStatus,
    SimulationStatus,
)
from xolotl.modules.monitoring.rules.lifecycle import (
    APPROVERS,
    WRITERS,
    activate_version,
    approve_version,
    evaluate_rule_actions,
    reject_version,
    retire_version,
    submit_version,
)
from xolotl.modules.monitoring.rules.models import (
    MonitoringRuleVersion,
    RuleAssignment,
    RuleSimulation,
)
from xolotl.modules.monitoring.rules.versions import WRITERS as VERSION_WRITERS
from xolotl.modules.monitoring.rules.versions import VersionInput
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.config import get_settings
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.errors import ForbiddenError, NotFoundError, ValidationFailedError
from xolotl.platform.events.event import event_from_row
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import PostgresJobQueue
from xolotl.platform.policy import load_jurisdictions
from xolotl.platform.policy.jurisdictions import JURISDICTIONS_FILE

STATES = list(RuleVersionStatus)
ROLES = [Role.ANALYST, Role.SENIOR_ANALYST, Role.MLRO, Role.AUDITOR_RO, Role.ADMIN]
SIMULATIONS = ["none", "fresh", "stale", "other_hash"]
HASH = b"\x01" * 32
SAME_AUTHOR_SQL = (
    "UPDATE monitoring_rule_version SET approved_by = author_id WHERE id = :id"
)

S = RuleVersionStatus
WRITE = frozenset({Role.SENIOR_ANALYST, Role.MLRO, Role.ADMIN})
APPROVE = frozenset({Role.MLRO, Role.ADMIN})
# The rows of the spec §9.4 table, one per action: (from states, who, who must or
# must not act as the author, needs a fresh simulation of the current definition).
SPEC: dict[
    RuleAction, tuple[frozenset[RuleVersionStatus], frozenset[Role], str, bool]
] = {
    RuleAction.EDIT: (frozenset({S.DRAFT, S.SIMULATED}), WRITE, "anyone", False),
    RuleAction.SIMULATE: (frozenset({S.DRAFT, S.SIMULATED}), WRITE, "anyone", False),
    RuleAction.SUBMIT: (frozenset({S.SIMULATED}), WRITE, "author", True),
    RuleAction.APPROVE: (frozenset({S.IN_REVIEW}), APPROVE, "not_author", True),
    RuleAction.REJECT: (frozenset({S.IN_REVIEW}), APPROVE, "anyone", False),
    RuleAction.ACTIVATE: (frozenset({S.APPROVED}), APPROVE, "anyone", False),
    RuleAction.RETIRE: (frozenset({S.ACTIVE}), APPROVE, "anyone", False),
}


def _expected(
    state: RuleVersionStatus,
    role: Role,
    *,
    same_user: bool,
    simulation: str,
    action: RuleAction,
) -> list[RuleBlockedReason]:
    states, who, author, needs_simulation = SPEC[action]
    reasons: list[RuleBlockedReason] = []
    if state not in states:
        reasons.append(RuleBlockedReason.STATE)
    if role not in who:
        reasons.append(RuleBlockedReason.ROLE_REQUIRED)
    if author == "author" and not same_user:
        reasons.append(RuleBlockedReason.NOT_AUTHOR)
    if author == "not_author" and same_user:
        reasons.append(RuleBlockedReason.SAME_AUTHOR)
    if needs_simulation and simulation == "none":
        reasons.append(RuleBlockedReason.SIMULATION_MISSING)
    if needs_simulation and simulation in {"stale", "other_hash"}:
        reasons.append(RuleBlockedReason.SIMULATION_STALE)
    return reasons


def _simulation(kind: str) -> RuleSimulation | None:
    if kind == "none":
        return None
    finished = NOW - timedelta(days=31 if kind == "stale" else 1)
    return RuleSimulation(
        status=SimulationStatus.COMPLETED,
        finished_at=finished,
        definition_hash=b"\x02" * 32 if kind == "other_hash" else HASH,
    )


def _version(state: RuleVersionStatus, author_id: UUID) -> MonitoringRuleVersion:
    return MonitoringRuleVersion(
        status=state, author_id=author_id, definition_hash=HASH
    )


@pytest.mark.parametrize(
    ("state", "role", "same_user", "simulation", "action"),
    list(
        itertools.product(STATES, ROLES, [False, True], SIMULATIONS, list(RuleAction))
    ),
)
def test_the_lifecycle_matrix_is_exactly_the_spec(
    state, role, same_user, simulation, action
):
    user_id, other = uuid4(), uuid4()
    actions = evaluate_rule_actions(
        _version(state, user_id if same_user else other),
        simulation=_simulation(simulation),
        roles=frozenset({role}),
        user_id=user_id,
        now=NOW,
    )
    expected = _expected(
        state, role, same_user=same_user, simulation=simulation, action=action
    )
    if expected:
        assert action not in actions.allowed
        assert list(actions.blocked[action]) == expected
    else:
        assert action in actions.allowed
        assert action not in actions.blocked
    assert set(actions.allowed) | set(actions.blocked) == set(RuleAction)


def test_the_role_sets_are_the_spec():
    assert WRITERS == WRITE
    assert APPROVERS == APPROVE
    # B5 owns the writers; the lifecycle reuses them.
    assert WRITERS is VERSION_WRITERS


def test_a_simulation_that_has_not_completed_is_missing():
    user_id = uuid4()
    for status in (
        SimulationStatus.QUEUED,
        SimulationStatus.RUNNING,
        SimulationStatus.FAILED,
    ):
        simulation = RuleSimulation(
            status=status, finished_at=NOW, definition_hash=HASH
        )
        actions = evaluate_rule_actions(
            _version(S.SIMULATED, user_id),
            simulation=simulation,
            roles=frozenset({Role.SENIOR_ANALYST}),
            user_id=user_id,
            now=NOW,
        )
        assert actions.blocked[RuleAction.SUBMIT] == (
            RuleBlockedReason.SIMULATION_MISSING,
        )


def test_the_configured_age_makes_a_simulation_stale(monkeypatch):
    settings = get_settings().model_copy(
        update={"monitoring_simulation_max_age_days": 7}
    )
    monkeypatch.setattr(lifecycle, "get_settings", lambda: settings)
    user_id = uuid4()

    def blocked(age: timedelta) -> tuple[RuleBlockedReason, ...]:
        simulation = RuleSimulation(
            status=SimulationStatus.COMPLETED,
            finished_at=NOW - age,
            definition_hash=HASH,
        )
        actions = evaluate_rule_actions(
            _version(S.SIMULATED, user_id),
            simulation=simulation,
            roles=frozenset({Role.MLRO}),
            user_id=user_id,
            now=NOW,
        )
        return actions.blocked.get(RuleAction.SUBMIT, ())

    assert blocked(timedelta(days=7) - timedelta(seconds=1)) == ()
    assert blocked(timedelta(days=7)) == (RuleBlockedReason.SIMULATION_STALE,)


def test_activation_is_blocked_by_a_parameter_out_of_bounds():
    actions = evaluate_rule_actions(
        _version(S.APPROVED, uuid4()),
        simulation=None,
        roles=frozenset({Role.MLRO}),
        user_id=uuid4(),
        now=NOW,
        out_of_bounds=True,
    )
    assert actions.blocked[RuleAction.ACTIVATE] == (
        RuleBlockedReason.PARAM_OUT_OF_BOUNDS,
    )


# ---- the transitions, in the database ------------------------------------------


pytestmark = pytest.mark.usefixtures("frozen")


@pytest.fixture
def library(admin_engine: Engine) -> None:
    load_library(admin_engine)


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze(monkeypatch)


@pytest.fixture
def approver(tenant: CreatedTenant) -> UUID:
    return add_staff(tenant, Role.MLRO)


def _rule_events(tenant: CreatedTenant) -> list[tuple[str, dict[str, object]]]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        rows = s.scalars(
            select(OutboxRow)
            .where(OutboxRow.type.in_([RULE_VERSION_ACTIVATED, RULE_VERSION_RETIRED]))
            .order_by(OutboxRow.seq)
        ).all()
        return [(row.type, dict(event_from_row(row).data)) for row in rows]


def _rule_audit(tenant: CreatedTenant) -> list[tuple[str, str | None, dict]]:
    return [
        (event.action, event.entity_id, dict(event.detail or {}))
        for event in audit_events(tenant.tenant_id)
        if event.action.startswith("rules.") and event.action != "rules.version_created"
    ]


def _assignments(tenant: CreatedTenant) -> list[RuleAssignment]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return list(
            s.scalars(select(RuleAssignment).order_by(RuleAssignment.effective_from))
        )


def _get(tenant: CreatedTenant, version_id: UUID) -> MonitoringRuleVersion:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return s.get_one(MonitoringRuleVersion, (tenant.tenant_id, version_id))


def _blocked(caught: pytest.ExceptionInfo[RuleTransitionNotAllowedError]) -> list[str]:
    assert caught.value.status == 409
    assert caught.value.code == "RULE_TRANSITION_NOT_ALLOWED"
    return list(caught.value.blocked_reasons)


@pytest.mark.usefixtures("library")
def test_an_author_submits_and_another_approver_activates(
    tenant: CreatedTenant, approver: UUID, queue: PostgresJobQueue
):
    segment = SegmentFilter(tier=["N2", "N3"])
    version = draft(tenant, body=VersionInput(segment=segment))
    simulation_id = simulate(tenant, version)
    with staff_session(tenant.tenant_id, tenant.admin_user_id, AUTHOR_ROLES) as s:
        in_review = submit_version(s, version.id)
    assert in_review.status is S.IN_REVIEW
    assert in_review.submitted_at == NOW
    with staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s:
        approved_version = approve_version(s, version.id)
    assert approved_version.status is S.APPROVED
    assert (approved_version.approved_by, approved_version.approved_at) == (
        approver,
        NOW,
    )
    with staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s:
        active = activate_version(
            s,
            version.id,
            effective_from=date(2026, 11, 1),
            product="EMONEY",
            queue=queue,
        )
    assert (active.status, active.activated_at) == (S.ACTIVE, NOW)
    assert active.simulation_id == simulation_id
    [assignment] = _assignments(tenant)
    assert assignment.rule_version_id == version.id
    assert (assignment.product, assignment.effective_from, assignment.effective_to) == (
        "EMONEY",
        date(2026, 11, 1),
        None,
    )
    assert assignment.approved_by == approver
    assert assignment.segment_filter == segment.model_dump(mode="json")
    assert _rule_events(tenant) == [
        (
            RULE_VERSION_ACTIVATED,
            {
                "rule_id": str(version.rule_id),
                "rule_version_id": str(version.id),
                "assignment_id": str(assignment.id),
            },
        )
    ]
    assert _rule_audit(tenant) == [
        (
            "rules.version_submitted",
            str(version.id),
            {"simulation_id": str(simulation_id)},
        ),
        (
            "rules.version_approved",
            str(version.id),
            {"simulation_id": str(simulation_id)},
        ),
        (
            "rules.version_activated",
            str(version.id),
            {
                "assignment_id": str(assignment.id),
                "effective_from": "2026-11-01",
                "product": "EMONEY",
                "category": "CHANGE_POLICY",
            },
        ),
    ]


@pytest.mark.usefixtures("library")
def test_the_author_cannot_approve(tenant: CreatedTenant):
    version = submitted(tenant)
    with (
        pytest.raises(RuleTransitionNotAllowedError) as caught,
        staff_session(tenant.tenant_id, tenant.admin_user_id, APPROVER_ROLES) as s,
    ):
        _ = approve_version(s, version.id)
    assert _blocked(caught) == ["SAME_AUTHOR"]
    assert _get(tenant, version.id).status is S.IN_REVIEW


@pytest.mark.usefixtures("library")
def test_only_the_author_submits(tenant: CreatedTenant, approver: UUID):
    version = draft(tenant)
    _ = simulate(tenant, version)
    with (
        pytest.raises(RuleTransitionNotAllowedError) as caught,
        staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s,
    ):
        _ = submit_version(s, version.id)
    assert _blocked(caught) == ["NOT_AUTHOR"]


@pytest.mark.usefixtures("library")
def test_a_stale_simulation_blocks_approval(tenant: CreatedTenant, approver: UUID):
    version = submitted(tenant)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        simulation_id = s.get_one(
            MonitoringRuleVersion, (tenant.tenant_id, version.id)
        ).simulation_id
        _ = s.execute(
            update(RuleSimulation)
            .where(RuleSimulation.id == simulation_id)
            .values(finished_at=NOW - timedelta(days=31))
        )
    with (
        pytest.raises(RuleTransitionNotAllowedError) as caught,
        staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s,
    ):
        _ = approve_version(s, version.id)
    assert _blocked(caught) == ["SIMULATION_STALE"]
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = s.execute(
            update(RuleSimulation)
            .where(RuleSimulation.id == simulation_id)
            .values(finished_at=NOW, definition_hash=b"\x09" * 32)
        )
    with (
        pytest.raises(RuleTransitionNotAllowedError) as caught,
        staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s,
    ):
        _ = approve_version(s, version.id)
    assert _blocked(caught) == ["SIMULATION_STALE"]


@pytest.mark.usefixtures("library")
def test_a_stale_simulation_blocks_submission(tenant: CreatedTenant):
    version = draft(tenant)
    _ = simulate(tenant, version, finished_at=NOW - timedelta(days=30))
    with (
        pytest.raises(RuleTransitionNotAllowedError) as caught,
        staff_session(tenant.tenant_id, tenant.admin_user_id, AUTHOR_ROLES) as s,
    ):
        _ = submit_version(s, version.id)
    assert _blocked(caught) == ["SIMULATION_STALE"]


@pytest.mark.usefixtures("library")
def test_a_version_without_a_simulation_cannot_be_submitted(tenant: CreatedTenant):
    version = draft(tenant)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = s.execute(
            update(MonitoringRuleVersion)
            .where(MonitoringRuleVersion.id == version.id)
            .values(status=S.SIMULATED)
        )
    with (
        pytest.raises(RuleTransitionNotAllowedError) as caught,
        staff_session(tenant.tenant_id, tenant.admin_user_id, AUTHOR_ROLES) as s,
    ):
        _ = submit_version(s, version.id)
    assert _blocked(caught) == ["SIMULATION_MISSING"]
    with (
        pytest.raises(RuleTransitionNotAllowedError) as caught,
        staff_session(tenant.tenant_id, tenant.admin_user_id, AUTHOR_ROLES) as s,
    ):
        _ = submit_version(s, draft(tenant).id)
    assert _blocked(caught) == ["STATE", "SIMULATION_MISSING"]


@pytest.mark.usefixtures("library")
def test_a_rejected_version_returns_to_draft_with_its_reason(
    tenant: CreatedTenant, approver: UUID
):
    version = submitted(tenant)
    for reason in ("", "   ", "x" * 501):
        with (
            pytest.raises(ValidationFailedError),
            staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s,
        ):
            _ = reject_version(s, version.id, reason=reason)
    with staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s:
        rejected = reject_version(s, version.id, reason="  umbral demasiado bajo ")
    assert rejected.status is S.DRAFT
    assert _rule_audit(tenant)[-1] == (
        "rules.version_rejected",
        str(version.id),
        {"reason": "umbral demasiado bajo"},
    )
    with (
        pytest.raises(RuleTransitionNotAllowedError) as caught,
        staff_session(tenant.tenant_id, approver, (Role.SENIOR_ANALYST,)) as s,
    ):
        _ = reject_version(s, version.id, reason="otra vez")
    assert _blocked(caught) == ["STATE", "ROLE_REQUIRED"]


@pytest.mark.usefixtures("library")
def test_activation_retires_the_previous_version_and_closes_its_assignment(
    tenant: CreatedTenant, approver: UUID, queue: PostgresJobQueue
):
    v1 = activated(tenant, approver, queue, effective_from=date(2026, 11, 1))
    v2 = activated(tenant, approver, queue, effective_from=date(2026, 12, 15))
    assert v2.rule_id == v1.rule_id
    old = _get(tenant, v1.id)
    assert (old.status, old.retired_at, old.retired_by_version_id) == (
        S.RETIRED,
        NOW,
        v2.id,
    )
    first, second = _assignments(tenant)
    assert (first.rule_version_id, first.effective_to) == (v1.id, date(2026, 12, 14))
    assert (second.rule_version_id, second.effective_to) == (v2.id, None)
    with staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s:
        assert [
            r.rule_version_id
            for r in active_rules(s, on=date(2026, 12, 14), product=None).rules
        ] == [v1.id]
        assert [
            r.rule_version_id
            for r in active_rules(s, on=date(2026, 12, 15), product=None).rules
        ] == [v2.id]
    retired = (
        RULE_VERSION_RETIRED,
        {
            "rule_id": str(v1.rule_id),
            "rule_version_id": str(v1.id),
            "assignment_id": str(first.id),
        },
    )
    assert retired in _rule_events(tenant)
    assert (
        "rules.version_retired",
        str(v1.id),
        {"assignment_id": str(first.id), "retired_by_version_id": str(v2.id)},
    ) in _rule_audit(tenant)


@pytest.mark.usefixtures("library")
def test_activation_ends_every_later_assignment_of_the_rule(
    tenant: CreatedTenant, approver: UUID, queue: PostgresJobQueue
):
    # v1 runs until v2 starts on 15 December; v3 then starts on 10 December, so
    # v1 must stop on the 9th as well, or both would apply from the 10th.
    v1 = activated(tenant, approver, queue, effective_from=date(2026, 11, 1))
    v2 = activated(tenant, approver, queue, effective_from=date(2026, 12, 15))
    v3 = activated(tenant, approver, queue, effective_from=date(2026, 12, 10))
    ends = {a.rule_version_id: a.effective_to for a in _assignments(tenant)}
    assert ends == {
        v1.id: date(2026, 12, 9),
        v2.id: date(2026, 12, 9),
        v3.id: None,
    }
    with staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s:
        for day in (date(2026, 12, 10), date(2026, 12, 20)):
            rules = active_rules(s, on=day, product=None).rules
            assert [r.rule_version_id for r in rules] == [v3.id]


@pytest.mark.usefixtures("library")
def test_activation_in_the_past_is_refused(
    tenant: CreatedTenant, approver: UUID, queue: PostgresJobQueue
):
    version = approved(tenant, approver)
    with (
        pytest.raises(ValidationFailedError),
        staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s,
    ):
        _ = activate_version(
            s,
            version.id,
            effective_from=TODAY - timedelta(days=1),
            product=None,
            queue=queue,
        )
    assert _get(tenant, version.id).status is S.APPROVED
    assert _assignments(tenant) == []


@pytest.mark.usefixtures("library")
def test_retire_closes_the_assignment_today(
    tenant: CreatedTenant, approver: UUID, queue: PostgresJobQueue
):
    version = activated(tenant, approver, queue)
    with staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s:
        retired = retire_version(s, version.id)
    assert (retired.status, retired.retired_at, retired.retired_by_version_id) == (
        S.RETIRED,
        NOW,
        None,
    )
    [assignment] = _assignments(tenant)
    assert assignment.effective_to == TODAY
    assert _rule_events(tenant)[-1] == (
        RULE_VERSION_RETIRED,
        {
            "rule_id": str(version.rule_id),
            "rule_version_id": str(version.id),
            "assignment_id": str(assignment.id),
        },
    )
    assert _rule_audit(tenant)[-1] == (
        "rules.version_retired",
        str(version.id),
        {"assignment_id": str(assignment.id), "retired_by_version_id": None},
    )
    with (
        pytest.raises(RuleTransitionNotAllowedError) as caught,
        staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s,
    ):
        _ = retire_version(s, version.id)
    assert _blocked(caught) == ["STATE"]


@pytest.mark.usefixtures("library")
def test_a_writer_who_is_not_an_approver_cannot_activate(
    tenant: CreatedTenant, approver: UUID, queue: PostgresJobQueue
):
    version = approved(tenant, approver)
    with (
        pytest.raises(RuleTransitionNotAllowedError) as caught,
        staff_session(tenant.tenant_id, approver, (Role.SENIOR_ANALYST,)) as s,
    ):
        _ = activate_version(
            s, version.id, effective_from=TODAY, product=None, queue=queue
        )
    assert _blocked(caught) == ["ROLE_REQUIRED"]


@pytest.mark.usefixtures("library")
def test_transitions_are_staff_only(tenant: CreatedTenant, queue: PostgresJobQueue):
    version = draft(tenant)
    _ = simulate(tenant, version)

    def activate(s: Session) -> MonitoringRuleVersion:
        return activate_version(
            s, version.id, effective_from=TODAY, product=None, queue=queue
        )

    def reject(s: Session) -> MonitoringRuleVersion:
        return reject_version(s, version.id, reason="no")

    calls = [
        partial(submit_version, version_id=version.id),
        partial(approve_version, version_id=version.id),
        reject,
        activate,
        partial(retire_version, version_id=version.id),
    ]
    for call in calls:
        with (
            pytest.raises(ForbiddenError),
            tenant_session(system_ctx(tenant.tenant_id)) as s,
        ):
            _ = call(s)


@pytest.mark.usefixtures("library")
def test_another_tenant_cannot_see_or_move_a_version(tenant: CreatedTenant):
    version = submitted(tenant)
    other = new_tenant("otra", "admin@otra.mx")
    with (
        pytest.raises(NotFoundError),
        staff_session(other.tenant_id, other.admin_user_id, APPROVER_ROLES) as s,
    ):
        _ = approve_version(s, version.id)
    assert _get(tenant, version.id).status is S.IN_REVIEW


@pytest.mark.usefixtures("library")
def test_the_check_refuses_a_same_author_approval_in_sql(tenant: CreatedTenant):
    version = submitted(tenant)
    with (
        pytest.raises(IntegrityError, match="approver_not_author"),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = s.execute(text(SAME_AUTHOR_SQL), {"id": version.id})


@pytest.mark.usefixtures("library")
def test_activation_ensures_the_declarations(
    tenant: CreatedTenant, approver: UUID, queue: PostgresJobQueue
):
    version = activated(tenant, approver, queue)
    lists = load_jurisdictions(get_settings().packs_dir / JURISDICTIONS_FILE.name)
    definition = resolve_definition(
        RuleDefinition.model_validate(version.definition), lists
    )
    expected = sorted({d.agg_id() for d in definition.aggregates})
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        declared = sorted(s.scalars(select(AggDeclaration.agg_id)))
        keys = s.scalars(select(JobRow.key).where(JobRow.kind == AGG_BACKFILL_KIND))
        jobs = sorted(key or "" for key in keys)
    assert declared == expected
    assert jobs == [f"agg_backfill:{agg_id}" for agg_id in expected]
