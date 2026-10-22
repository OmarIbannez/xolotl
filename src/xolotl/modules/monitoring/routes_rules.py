"""The rules API: templates, rules, versions, lifecycle, simulations (plan 4b B9).

Templates, rules and simulations are read by staff and by API clients with
``rules:read`` (spec §15); every write, the lifecycle and the assignments are
staff only. A simulation's ``sample`` and ``overlap`` say which customers a rule
would hit, so an API client's copy carries neither (tipping-off, PRV-502), and only
staff see the actions they may take on a version. Writers (SENIOR_ANALYST, MLRO,
ADMIN) create, tune and simulate versions; the lifecycle routes admit any staff
writer and the service decides by the spec §9.4 matrix, answering 409
``RULE_TRANSITION_NOT_ALLOWED`` with ``blocked_reasons``. The staff view evaluates
the same matrix for the caller, with the same editors and bounds the service
reads, so a reason it shows is the reason a refusal would give.

Every definition stored goes through B5's services, which validate it as a
``RuleDefinition`` (plan 4b Deviation 19 N1). Another tenant's rule, version or
simulation is ``NOT_FOUND`` (row-level security). Routes hold no business rule.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import starmap
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Query
from pydantic import JsonValue, ValidationError
from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.routes_transactions import QueueDep
from xolotl.modules.monitoring.rules import versions as rule_versions
from xolotl.modules.monitoring.rules.assignments import assignments_in_force
from xolotl.modules.monitoring.rules.definitions import (
    AnyParam,
    ParamValue,
    RuleDefinition,
    RuleFamily,
    RuleKind,
)
from xolotl.modules.monitoring.rules.enums import (
    RuleAction,
    RuleBlockedReason,
    RuleVersionStatus,
    SimulationStatus,
)
from xolotl.modules.monitoring.rules.lifecycle import (
    activate_version,
    approve_version,
    evaluate_rule_actions,
    reject_version,
    retire_version,
    submit_version,
    version_editors,
    version_out_of_bounds,
)
from xolotl.modules.monitoring.rules.models import (
    MonitoringRule,
    MonitoringRuleVersion,
    RuleAssignment,
    RuleSimulation,
    RuleTemplate,
    RuleTemplateVersion,
)
from xolotl.modules.monitoring.rules.templates import (
    definition_of,
    latest_template_versions,
    template_version,
)
from xolotl.modules.monitoring.rules.versions import WRITERS, VersionInput
from xolotl.modules.monitoring.schemas import (
    ActivateRequest,
    ParamView,
    RejectRequest,
    RuleAssignmentResponse,
    RuleCreateRequest,
    RuleQuery,
    RuleResponse,
    SimulateAccepted,
    SimulateRequest,
    SimulationResponse,
    TemplateResponse,
    VersionRequest,
    VersionResponse,
)
from xolotl.modules.monitoring.simulation.job import request_simulation
from xolotl.platform import clock
from xolotl.platform.api.pagination import Page, PageDep, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.config import get_settings
from xolotl.platform.db import bound_context
from xolotl.platform.errors import NotFoundError, ValidationFailedError

router = v1_router(prefix="/monitoring", tags=["monitoring-rules"])

RulesReader = Annotated[Actor, Depends(require_access(scope="rules:read"))]
RuleWriter = Annotated[Actor, Depends(require_access(roles=WRITERS, write=True))]
# Staff only (no scope); the lifecycle service checks the roles (spec §9.4).
RuleStaff = Annotated[Actor, Depends(require_access(write=True))]
StaffReader = Annotated[Actor, Depends(require_access())]

SENTENCE_LOCALE = "es-MX"
# A profile rule's history branch needs three months of history before the month it
# compares, so a shorter range never fires it (plan 4b Deviation 13).
PROFILE_HISTORY_DAYS = 120
PROFILE_HISTORY_SHORT = "PROFILE_HISTORY_SHORT"
_PARAM_TYPES: dict[type, Literal["DECIMAL", "INT", "STR", "BOOL"]] = {
    Decimal: "DECIMAL",
    int: "INT",
    str: "STR",
    bool: "BOOL",
}


def _spelled(value: ParamValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _optional(value: ParamValue | None) -> str | None:
    return None if value is None else _spelled(value)


def _param_view(name: str, param: AnyParam, source: str) -> ParamView:
    return ParamView(
        name=name,
        value=_spelled(param.value),
        type=_PARAM_TYPES[type(param.value)],
        min=_optional(param.min),
        max=_optional(param.max),
        choices=None
        if param.choices is None
        else [_spelled(choice) for choice in param.choices],
        tighten=param.tighten,
        unit=param.unit,
        source=source,
    )


def _param_views(definition: RuleDefinition, source: str) -> list[ParamView]:
    return [
        _param_view(name, param, source) for name, param in definition.params.items()
    ]


def _template_source(template: RuleTemplateVersion) -> str:
    return f"template {template.template_code} v{template.version}"


def _template_view(row: RuleTemplate, version: RuleTemplateVersion) -> TemplateResponse:
    definition = definition_of(version)
    return TemplateResponse(
        code=row.code,
        family=RuleFamily(row.family),
        kind=RuleKind(row.kind),
        title=row.title,
        typology=list(row.typology),
        country_code=row.country_code,
        packs=list(row.packs),
        version=version.version,
        params=_param_views(definition, _template_source(version)),
        definition=version.definition,
        sentences={
            "es-MX": definition.render("es-MX"),
            "en": definition.render("en"),
        },
    )


def _notes(simulation: RuleSimulation, kind: RuleKind) -> list[str]:
    span = (simulation.range_end - simulation.range_start).days + 1
    if kind is RuleKind.PROFILE_DEVIATION and span < PROFILE_HISTORY_DAYS:
        return [PROFILE_HISTORY_SHORT]
    return []


def _valid_until(simulation: RuleSimulation) -> date | None:
    if (
        simulation.status is not SimulationStatus.COMPLETED
        or simulation.finished_at is None
    ):
        return None
    days = get_settings().monitoring_simulation_max_age_days
    return policy_date(simulation.finished_at + timedelta(days=days))


def _simulation_view(
    simulation: RuleSimulation, *, kind: RuleKind, staff: bool
) -> SimulationResponse:
    # Which customers a rule would hit is for staff only (PRV-502).
    overlap: dict[str, int] | None = None
    sample: list[dict[str, JsonValue]] = []
    if staff and simulation.overlap is not None:
        overlap = {
            name: count
            for name, count in simulation.overlap.items()
            if isinstance(count, int)
        }
    if staff:
        sample = [item for item in simulation.sample or [] if isinstance(item, dict)]
    return SimulationResponse(
        id=simulation.id,
        rule_version_id=simulation.rule_version_id,
        status=simulation.status,
        range_start=simulation.range_start,
        range_end=simulation.range_end,
        definition_hash=simulation.definition_hash.hex(),
        transactions_read=simulation.transactions_read,
        hit_count=simulation.hit_count,
        customers_hit=simulation.customers_hit,
        hits_per_week=[
            item for item in simulation.hits_per_week or [] if isinstance(item, dict)
        ],
        overlap=overlap,
        sample=sample,
        requested_by=simulation.requested_by,
        started_at=simulation.started_at,
        finished_at=simulation.finished_at,
        last_error=simulation.last_error,
        valid_until=_valid_until(simulation),
        notes=_notes(simulation, kind),
    )


def _assignment_view(row: RuleAssignment, rule_code: str) -> RuleAssignmentResponse:
    segment = row.segment_filter
    return RuleAssignmentResponse(
        id=row.id,
        rule_version_id=row.rule_version_id,
        rule_code=rule_code,
        product=row.product,
        segment_filter=None
        if segment is None
        else {
            name: [str(item) for item in values]
            for name, values in segment.items()
            if isinstance(values, list)
        },
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        approved_by=row.approved_by,
    )


@dataclass(frozen=True, slots=True)
class _Related:
    """What the views of some versions need, read in a few queries."""

    # Every simulation a version links or is the latest of, by id.
    simulations: dict[UUID, RuleSimulation]
    # The latest simulation of each version, by version id.
    latest: dict[UUID, RuleSimulation]
    # The assignment of each activated version, by version id.
    assignments: dict[UUID, RuleAssignment]


def _related(s: Session, versions: Sequence[MonitoringRuleVersion]) -> _Related:
    ids = [version.id for version in versions]
    if not ids:
        return _Related({}, {}, {})
    latest = {
        row.rule_version_id: row
        for row in s.scalars(
            select(RuleSimulation)
            .where(RuleSimulation.rule_version_id.in_(ids))
            .order_by(
                RuleSimulation.rule_version_id,
                RuleSimulation.created_at.desc(),
                RuleSimulation.id.desc(),
            )
            .ext(distinct_on(RuleSimulation.rule_version_id))
        )
    }
    simulations = {row.id: row for row in latest.values()}
    linked = {
        version.simulation_id
        for version in versions
        if version.simulation_id is not None
    } - set(simulations)
    if linked:
        simulations.update(
            (row.id, row)
            for row in s.scalars(
                select(RuleSimulation).where(RuleSimulation.id.in_(linked))
            )
        )
    assignments = {
        row.rule_version_id: row
        for row in s.scalars(
            select(RuleAssignment)
            .where(RuleAssignment.rule_version_id.in_(ids))
            .order_by(RuleAssignment.created_at, RuleAssignment.id)
        )
    }
    template_ids = {
        version.template_version_id
        for version in versions
        if version.template_version_id is not None
    }
    if template_ids:
        # Into the identity map, for the sources and the bounds check.
        _ = s.scalars(
            select(RuleTemplateVersion).where(RuleTemplateVersion.id.in_(template_ids))
        ).all()
    return _Related(simulations, latest, assignments)


def _source(s: Session, version: MonitoringRuleVersion, rule: MonitoringRule) -> str:
    # Where the version's bounds come from (plan 4b Deviation 17).
    if version.template_version_id is None:
        return f"rule {rule.code} v{version.version}"
    template = s.get_one(RuleTemplateVersion, version.template_version_id)
    return _template_source(template)


@dataclass(frozen=True, slots=True)
class _Viewer:
    """Who reads a version, and when: staff see their actions, API clients do not."""

    actor: Actor
    now: datetime

    @property
    def staff(self) -> bool:
        return self.actor.is_staff


def _version_view(
    s: Session,
    version: MonitoringRuleVersion,
    *,
    rule: MonitoringRule,
    related: _Related,
    viewer: _Viewer,
) -> VersionResponse:
    definition = RuleDefinition.model_validate(version.definition)
    latest = related.latest.get(version.id)
    assignment = related.assignments.get(version.id)
    allowed: list[RuleAction] = []
    blocked: dict[RuleAction, list[RuleBlockedReason]] = {}
    if viewer.staff:
        ctx = viewer.actor.context
        actions = evaluate_rule_actions(
            version,
            simulation=None
            if version.simulation_id is None
            else related.simulations.get(version.simulation_id),
            roles=ctx.roles,
            user_id=UUID(ctx.actor_id),
            now=viewer.now,
            out_of_bounds=version_out_of_bounds(s, version),
            editors=version_editors(s, version.id),
        )
        allowed = list(actions.allowed)
        blocked = {action: list(reasons) for action, reasons in actions.blocked.items()}
    return VersionResponse(
        id=version.id,
        rule_id=version.rule_id,
        version=version.version,
        status=version.status,
        definition_hash=version.definition_hash.hex(),
        sync=version.sync,
        params=_param_views(definition, _source(s, version, rule)),
        segment=definition.segment,
        severity=definition.severity,
        dedup=definition.dedup,
        sentence=definition.render(SENTENCE_LOCALE),
        author_id=version.author_id,
        submitted_at=version.submitted_at,
        approved_by=version.approved_by,
        approved_at=version.approved_at,
        simulation=None
        if latest is None
        else _simulation_view(latest, kind=definition.kind, staff=viewer.staff),
        assignment=None
        if assignment is None
        else _assignment_view(assignment, rule.code),
        activated_at=version.activated_at,
        retired_at=version.retired_at,
        retired_by_version_id=version.retired_by_version_id,
        allowed_actions=allowed,
        blocked_reasons=blocked,
        created_at=version.created_at,
    )


def _rule_views(
    s: Session, rules: Sequence[MonitoringRule], viewer: _Viewer
) -> list[RuleResponse]:
    if not rules:
        return []
    versions = list(
        s.scalars(
            select(MonitoringRuleVersion)
            .where(MonitoringRuleVersion.rule_id.in_([rule.id for rule in rules]))
            .order_by(MonitoringRuleVersion.rule_id, MonitoringRuleVersion.version)
        )
    )
    related = _related(s, versions)
    views: list[RuleResponse] = []
    for rule in rules:
        own = [version for version in versions if version.rule_id == rule.id]
        shown = [
            _version_view(s, version, rule=rule, related=related, viewer=viewer)
            for version in own
        ]
        simulations = [
            related.latest[version.id]
            for version in own
            if version.id in related.latest
        ]
        last = max(simulations, key=lambda row: (row.created_at, row.id), default=None)
        newest = RuleDefinition.model_validate(own[-1].definition)
        views.append(
            RuleResponse(
                id=rule.id,
                code=rule.code,
                family=RuleFamily(rule.family),
                kind=RuleKind(rule.kind),
                template_code=rule.template_code,
                title=rule.title,
                typology=list(newest.typology),
                created_by=rule.created_by,
                created_at=rule.created_at,
                versions=shown,
                active_version_id=next(
                    (
                        version.id
                        for version in own
                        if version.status is RuleVersionStatus.ACTIVE
                    ),
                    None,
                ),
                last_simulation=None
                if last is None
                else _simulation_view(last, kind=newest.kind, staff=viewer.staff),
            )
        )
    return views


def _viewer(actor: Actor) -> _Viewer:
    return _Viewer(actor=actor, now=clock.now())


def _one_version_view(
    s: Session, version: MonitoringRuleVersion, actor: Actor
) -> VersionResponse:
    rule = s.get_one(MonitoringRule, (version.tenant_id, version.rule_id))
    return _version_view(
        s, version, rule=rule, related=_related(s, [version]), viewer=_viewer(actor)
    )


def _rule_of(s: Session, rule_id: UUID) -> MonitoringRule:
    rule = s.get(MonitoringRule, (bound_context(s).require_tenant_id(), rule_id))
    if rule is None:
        msg = "rule not found"
        raise NotFoundError(msg)
    return rule


def _version_of(s: Session, rule_id: UUID, number: int) -> MonitoringRuleVersion:
    version = s.scalars(
        select(MonitoringRuleVersion).where(
            MonitoringRuleVersion.rule_id == rule_id,
            MonitoringRuleVersion.version == number,
        )
    ).one_or_none()
    if version is None:
        msg = "rule version not found"
        raise NotFoundError(msg)
    return version


def _rules_query(query: RuleQuery) -> Select[MonitoringRule]:
    stmt = select(MonitoringRule)
    if query.kind is not None:
        stmt = stmt.where(MonitoringRule.kind == query.kind.value)
    if query.typology is not None:
        typed = select(MonitoringRuleVersion.rule_id).where(
            MonitoringRuleVersion.definition["typology"].contains([query.typology])
        )
        stmt = stmt.where(MonitoringRule.id.in_(typed))
    if query.status is not None:
        in_status = select(MonitoringRuleVersion.rule_id).where(
            MonitoringRuleVersion.status == query.status
        )
        stmt = stmt.where(MonitoringRule.id.in_(in_status))
    return stmt


def _custom_definition(raw: dict[str, JsonValue]) -> RuleDefinition:
    try:
        return RuleDefinition.model_validate(raw)
    except ValidationError as exc:
        error = exc.errors()[0]
        where = ".".join(str(part) for part in ("definition", *error["loc"]))
        message = error["msg"].removeprefix("Value error, ")
        msg = f"{where}: {message}"
        raise ValidationFailedError(msg) from exc


@router.get("/templates")
def list_templates(_reader: RulesReader, session: SessionDep) -> list[TemplateResponse]:
    rows = {row.code: row for row in session.scalars(select(RuleTemplate))}
    return [
        _template_view(rows[version.template_code], version)
        for version in latest_template_versions(session)
    ]


@router.get("/templates/{code}")
def get_template(
    code: str, _reader: RulesReader, session: SessionDep
) -> TemplateResponse:
    version = template_version(session, code)
    return _template_view(session.get_one(RuleTemplate, code), version)


@router.get("/rules")
def list_rules(
    reader: RulesReader,
    session: SessionDep,
    page: PageDep,
    query: Annotated[RuleQuery, Query()],
) -> Page[RuleResponse]:
    rows, next_cursor = paginate(
        session,
        _rules_query(query),
        ts_col=MonitoringRule.created_at,
        id_col=MonitoringRule.id,
        key=lambda row: (row.created_at, row.id),
        page=page,
    )
    return Page(
        items=_rule_views(session, rows, _viewer(reader)), next_cursor=next_cursor
    )


@router.get("/rules/{rule_id}")
def get_rule(rule_id: UUID, reader: RulesReader, session: SessionDep) -> RuleResponse:
    [view] = _rule_views(session, [_rule_of(session, rule_id)], _viewer(reader))
    return view


@router.post("/rules", status_code=201)
def create_rule(
    body: RuleCreateRequest, writer: RuleWriter, session: SessionDep
) -> VersionResponse:
    if body.definition is not None:
        version = rule_versions.create_custom_rule(
            session, definition=_custom_definition(body.definition)
        )
    else:
        version = rule_versions.create_rule(
            session,
            template_code=str(body.template_code),
            body=VersionInput(
                params=body.params,
                segment=body.segment,
                severity=body.severity,
                dedup=body.dedup,
            ),
        )
    return _one_version_view(session, version, writer)


@router.post("/rules/{rule_id}/versions", status_code=201)
def create_rule_version(
    rule_id: UUID, body: VersionRequest, writer: RuleWriter, session: SessionDep
) -> VersionResponse:
    version = rule_versions.create_version(session, rule_id, body=body)
    return _one_version_view(session, version, writer)


@router.patch("/rules/{rule_id}/versions/{v}")
def update_rule_version(
    rule_id: UUID, v: int, body: VersionRequest, writer: RuleWriter, session: SessionDep
) -> VersionResponse:
    version = rule_versions.update_version(
        session, _version_of(session, rule_id, v).id, body=body
    )
    return _one_version_view(session, version, writer)


@router.post("/rules/{rule_id}/versions/{v}/simulate", status_code=202)
def simulate_rule_version(
    rule_id: UUID,
    v: int,
    body: SimulateRequest,
    _writer: RuleWriter,
    session: SessionDep,
    queue: QueueDep,
) -> SimulateAccepted:
    simulation = request_simulation(
        session,
        _version_of(session, rule_id, v).id,
        range_start=body.range_start,
        range_end=body.range_end,
        queue=queue,
    )
    return SimulateAccepted(simulation_id=simulation.id)


@router.get("/simulations/{simulation_id}")
def get_simulation(
    simulation_id: UUID, reader: RulesReader, session: SessionDep
) -> SimulationResponse:
    tenant_id = bound_context(session).require_tenant_id()
    simulation = session.get(RuleSimulation, (tenant_id, simulation_id))
    if simulation is None:
        msg = "simulation not found"
        raise NotFoundError(msg)
    version = session.get_one(
        MonitoringRuleVersion, (tenant_id, simulation.rule_version_id)
    )
    kind = RuleDefinition.model_validate(version.definition).kind
    return _simulation_view(simulation, kind=kind, staff=reader.is_staff)


@router.post("/rules/{rule_id}/versions/{v}/submit")
def submit_rule_version(
    rule_id: UUID, v: int, staff: RuleStaff, session: SessionDep
) -> VersionResponse:
    version = submit_version(session, _version_of(session, rule_id, v).id)
    return _one_version_view(session, version, staff)


@router.post("/rules/{rule_id}/versions/{v}/approve")
def approve_rule_version(
    rule_id: UUID, v: int, staff: RuleStaff, session: SessionDep
) -> VersionResponse:
    version = approve_version(session, _version_of(session, rule_id, v).id)
    return _one_version_view(session, version, staff)


@router.post("/rules/{rule_id}/versions/{v}/reject")
def reject_rule_version(
    rule_id: UUID, v: int, body: RejectRequest, staff: RuleStaff, session: SessionDep
) -> VersionResponse:
    version = reject_version(
        session, _version_of(session, rule_id, v).id, reason=body.reason
    )
    return _one_version_view(session, version, staff)


@router.post("/rules/{rule_id}/versions/{v}/activate")
def activate_rule_version(
    rule_id: UUID,
    v: int,
    body: ActivateRequest,
    staff: RuleStaff,
    session: SessionDep,
    queue: QueueDep,
) -> VersionResponse:
    version = activate_version(
        session,
        _version_of(session, rule_id, v).id,
        effective_from=body.effective_from,
        product=body.product,
        queue=queue,
    )
    return _one_version_view(session, version, staff)


@router.post("/rules/{rule_id}/versions/{v}/retire")
def retire_rule_version(
    rule_id: UUID, v: int, staff: RuleStaff, session: SessionDep
) -> VersionResponse:
    version = retire_version(session, _version_of(session, rule_id, v).id)
    return _one_version_view(session, version, staff)


@router.get("/assignments")
def list_assignments(
    _staff: StaffReader, session: SessionDep, on: date | None = None
) -> list[RuleAssignmentResponse]:
    day = on or policy_date(clock.now())
    return list(starmap(_assignment_view, assignments_in_force(session, on=day)))
