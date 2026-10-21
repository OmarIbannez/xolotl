"""Tenant rules and their versions, with parameters inside the template's bounds.

A tenant rule starts from a library template (spec §9.4): its first version copies
the template's definition with the tenant's ``params``, ``segment``, ``severity``
and ``dedup``, and every later version starts from the latest one. Values are
checked against the bounds of the template version the rule was created from
(``min``, ``max``, ``choices`` and the ``tighten`` direction against the template's
default); a violation is 422 ``RULE_PARAM_OUT_OF_BOUNDS`` naming the parameter,
the bound and its source. The declarations a kind derives from its parameters
(the structuring band, the velocity window) are rewritten from the tuned values,
so a changed parameter is a changed ``agg_id`` (reconciliation finding 27).

Writes run in the caller's transaction: rows, then audit.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import ClassVar, cast
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    JsonValue,
    ValidationError,
    field_validator,
)
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import (
    MAX_WINDOW_HOURS,
    AggregateDeclaration,
    AggregateFunction,
    window_hours,
    window_parts,
)
from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.errors import (
    RuleParamOutOfBoundsError,
    RuleTransitionNotAllowedError,
)
from xolotl.modules.monitoring.rules.definitions import (
    MONTH,
    AnyParam,
    Dedup,
    ParamValue,
    RuleDefinition,
    RuleFamily,
    RuleKind,
    SegmentFilter,
    Severity,
)
from xolotl.modules.monitoring.rules.enums import RuleBlockedReason, RuleVersionStatus
from xolotl.modules.monitoring.rules.models import (
    MonitoringRule,
    MonitoringRuleVersion,
    RuleTemplateVersion,
)
from xolotl.modules.monitoring.rules.templates import (
    definition_of,
    latest_template_versions,
    template_version,
)
from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.config import get_settings
from xolotl.platform.db import bound_context
from xolotl.platform.enums import Role
from xolotl.platform.errors import (
    DomainError,
    ErrorItem,
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
)
from xolotl.platform.policy.models import (
    PolicyPack,
    PolicyPackVersion,
    TenantPackAssignment,
)

# The roles that author and edit rule versions (spec §9.4).
WRITERS = frozenset({Role.SENIOR_ANALYST, Role.MLRO, Role.ADMIN})
_EDITABLE = frozenset({RuleVersionStatus.DRAFT, RuleVersionStatus.SIMULATED})
# The declarations a STRUCTURING rule counts over its band and window.
_BANDED = frozenset({"near_count", "near_sum"})
_WINDOWED = frozenset({AggregateFunction.COUNT, AggregateFunction.SUM})
# Migration 0013's UNIQUE (tenant_id, code) on monitoring_rule.
_RULE_CODE_UNIQUE = "uq_monitoring_rule_tenant_id_code"
# The longest VELOCITY window, in hours (spec §9.1: P7D).
_VELOCITY_HOURS = 7 * 24
_TYPE_NAMES: Mapping[type, str] = {
    Decimal: "decimal",
    int: "integer",
    str: "text",
    bool: "boolean",
}


def _float_param(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    items = cast("dict[object, object]", raw).items()
    return next((str(name) for name, item in items if isinstance(item, float)), None)


class VersionInput(BaseModel):
    """What a tenant may tune in a version; a field left out keeps its value."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    params: dict[str, ParamValue] | None = None
    segment: SegmentFilter | None = None
    severity: Severity | None = None
    dedup: Dedup | None = None

    @field_validator("params", mode="before")
    @classmethod
    def _no_floats(cls, value: object) -> object:
        # As in a definition: a JSON float is not an exact decimal.
        name = _float_param(value)
        if name is not None:
            msg = f"{name}: write a decimal as a string, never a float"
            raise ValueError(msg)
        return value


@dataclass(frozen=True, slots=True)
class BoundViolation:
    """A parameter value the template does not allow.

    ``bound`` is ``min``, ``max``, ``choices``, ``tighten`` or ``type``; ``limit``
    spells the bound's value; ``source`` names the template version it comes from.
    """

    param: str
    bound: str
    limit: str
    source: str

    def message(self) -> str:
        """Spell the violation for a problem item, without the submitted value.

        Returns:
            Such as ``outside min 1000 of template CASH_STRUCTURING v1``.

        """
        return f"outside {self.bound} {self.limit} of {self.source}"


def _coerced(param: AnyParam, value: ParamValue) -> ParamValue | None:
    # The template's type decides; an integer or a decimal string is a decimal.
    expected = type(param.value)
    if isinstance(value, bool) or expected is bool:
        return value if type(value) is expected else None
    if expected is Decimal:
        if isinstance(value, Decimal | int):
            return Decimal(value)
        try:
            number = Decimal(value)
        except ArithmeticError:
            return None
        return number if number.is_finite() else None
    return value if type(value) is expected else None


def _less(value: ParamValue, other: ParamValue) -> bool:
    # Values of one parameter share its type (``_coerced``), so they compare.
    return cast("Decimal", value) < cast("Decimal", other)


def _violation(param: AnyParam, value: ParamValue) -> tuple[str, str] | None:
    if param.choices is not None and value not in param.choices:
        return "choices", ", ".join(str(choice) for choice in param.choices)
    if param.min is not None and _less(value, param.min):
        return "min", str(param.min)
    if param.max is not None and _less(param.max, value):
        return "max", str(param.max)
    if param.tighten == "LOWER" and _less(param.value, value):
        return "tighten", f"LOWER from {param.value}"
    if param.tighten == "RAISE" and _less(value, param.value):
        return "tighten", f"RAISE from {param.value}"
    return None


def _window_violation(kind: RuleKind, window: str) -> tuple[str, str] | None:
    # The kind's own limits on a window (spec §8.2, §9.1), reported as bounds so
    # the editor can place them on the field (spec §9.4).
    parts = window_parts(window)
    if window == MONTH or parts is None:
        return "type", "an ISO duration such as P1D"
    months, days, hours = parts
    if months and (days or hours):
        return "type", "months or days, not both"
    length = window_hours(window) or 0
    if length < 1:
        return "min", "PT1H"
    if length > MAX_WINDOW_HOURS:
        return "max", "P6M"
    if kind is RuleKind.VELOCITY and length > _VELOCITY_HOURS:
        return "max", "P7D (VELOCITY window)"
    return None


def check_params(
    template: RuleDefinition, values: Mapping[str, ParamValue], *, source: str
) -> list[BoundViolation]:
    """Check tuned values against the template's parameters and bounds.

    ``tighten: LOWER`` allows only values at or below the template's default,
    ``RAISE`` only values at or above it.

    Returns:
        One violation per refused value, in the order of ``values``.

    """
    violations: list[BoundViolation] = []
    for name, value in values.items():
        param = template.params.get(name)
        if param is None:
            violations.append(BoundViolation(name, "type", "no such parameter", source))
            continue
        typed = _coerced(param, value)
        if typed is None:
            expected = _TYPE_NAMES[type(param.value)]
            violations.append(BoundViolation(name, "type", expected, source))
            continue
        found = _violation(param, typed)
        if found is None and name == "window" and isinstance(typed, str):
            found = _window_violation(template.kind, typed)
        if found is not None:
            violations.append(BoundViolation(name, *found, source))
    return violations


def _derive(
    data: dict[str, object],
    template: RuleDefinition,
    values: Mapping[str, ParamValue],
) -> None:
    """Write the declarations a kind derives from its parameters into ``data``.

    STRUCTURING: the band of ``near_count``/``near_sum`` is ``near_ratio`` times
    ``threshold`` up to ``threshold`` over ``window``, and the evidence follows.
    VELOCITY: every ``COUNT`` and ``SUM`` counts over ``window``, and the
    evidence window follows. ``data`` is the document about to be validated, so a
    tuned window never meets the old one in the definition's own checks.
    """
    evidence = template.evidence
    if template.kind is RuleKind.STRUCTURING:
        threshold = cast("Decimal", values["threshold"])
        low = cast("Decimal", values["near_ratio"]) * threshold
        window = str(values["window"])

        def band(declaration: AggregateDeclaration) -> AggregateDeclaration:
            if declaration.name not in _BANDED:
                return declaration
            where = replace(declaration.where, amount_gte=low, amount_lt=threshold)
            return replace(declaration, where=where, window=window)

        aggregates = [band(declaration) for declaration in template.aggregates]
        if evidence.transactions_where.amount_lt is not None:
            where = replace(
                evidence.transactions_where, amount_gte=low, amount_lt=threshold
            )
            evidence = evidence.model_copy(update={"transactions_where": where})
    elif template.kind is RuleKind.VELOCITY:
        window = str(values["window"])
        aggregates = [
            replace(declaration, window=window)
            if declaration.function in _WINDOWED
            else declaration
            for declaration in template.aggregates
        ]
    else:
        return
    if evidence.transactions_window is not None:
        evidence = evidence.model_copy(update={"transactions_window": window})
    data["aggregates"] = aggregates
    data["evidence"] = evidence


def coupling_problem(definition: RuleDefinition) -> str | None:
    """Say how a definition's derived declarations disagree with its parameters.

    Returns:
        The problem, or None when the band and windows follow the parameters.

    """
    data = definition.model_dump()
    _derive(data, definition, _values(definition))
    expected = RuleDefinition.model_validate(data)
    if expected.definition_hash() == definition.definition_hash():
        return None
    return "its declared band or windows disagree with its parameters"


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error["loc"])
    message = error["msg"].removeprefix("Value error, ")
    return f"{where}: {message}" if where else message


def build_definition(
    template: RuleDefinition, body: VersionInput, *, source: str
) -> RuleDefinition:
    """Apply a version's tuning to its template's definition.

    Returns:
        The tenant's definition, with the derived declarations rewritten.

    Raises:
        RuleParamOutOfBoundsError: With one item per refused parameter, located at
            ``("params", name)``.
        ValidationFailedError: When the tuned definition is not valid as a whole
            (a velocity window longer than seven days, a tier limit not deduplicated
            per month).

    """
    values = body.params or {}
    violations = check_params(template, values, source=source)
    if violations:
        raise RuleParamOutOfBoundsError(
            errors=[
                ErrorItem(
                    loc=("params", violation.param),
                    msg=violation.message(),
                    type="RULE_PARAM_OUT_OF_BOUNDS",
                )
                for violation in violations
            ]
        )
    tuned: dict[str, ParamValue] = {}
    params: dict[str, object] = {}
    for name, param in template.params.items():
        dumped = param.model_dump()
        given = values.get(name)
        typed = None if given is None else _coerced(param, given)
        tuned[name] = param.value if typed is None else typed
        dumped["value"] = tuned[name]
        params[name] = dumped
    data = template.model_dump()
    data["params"] = params
    _derive(data, template, tuned)
    if body.segment is not None:
        data["segment"] = body.segment
    if body.severity is not None:
        data["severity"] = body.severity
    if body.dedup is not None:
        data["dedup"] = body.dedup
    try:
        return RuleDefinition.model_validate(data)
    except ValidationError as exc:
        raise ValidationFailedError(_first_error(exc)) from exc


def _require_writer(s: Session) -> UUID:
    ctx = bound_context(s)
    if not WRITERS & ctx.roles:
        msg = "only a senior analyst, an MLRO or an admin writes rule versions"
        raise ForbiddenError(msg, code="ROLE_FORBIDDEN")
    return UUID(ctx.actor_id)


def _values(definition: RuleDefinition) -> dict[str, ParamValue]:
    return {name: param.value for name, param in definition.params.items()}


def _merged(base: RuleDefinition, body: VersionInput) -> VersionInput:
    # A field the body leaves out keeps the base version's value.
    return VersionInput(
        params={**_values(base), **(body.params or {})},
        segment=body.segment if body.segment is not None else base.segment,
        severity=body.severity or base.severity,
        dedup=body.dedup or base.dedup,
    )


def _source(template: RuleTemplateVersion) -> str:
    return f"template {template.template_code} v{template.version}"


def _audit_created(s: Session, version: MonitoringRuleVersion, code: str) -> None:
    _ = record(
        s,
        AuditEvent(
            action="rules.version_created",
            entity_type="monitoring_rule_version",
            entity_id=str(version.id),
            detail={
                "rule_id": str(version.rule_id),
                "rule_code": code,
                "version": version.version,
            },
        ),
    )


def _insert_version(
    s: Session,
    rule: MonitoringRule,
    *,
    number: int,
    definition: RuleDefinition,
    author_id: UUID,
    template_version_id: UUID | None,
) -> MonitoringRuleVersion:
    version = MonitoringRuleVersion(
        tenant_id=rule.tenant_id,
        rule_id=rule.id,
        version=number,
        template_version_id=template_version_id,
        definition=definition.model_dump(mode="json"),
        definition_hash=definition.definition_hash(),
        status=RuleVersionStatus.DRAFT,
        sync=definition.sync,
        author_id=author_id,
    )
    s.add(version)
    s.flush()
    _audit_created(s, version, rule.code)
    return version


def _new_rule(
    s: Session,
    definition: RuleDefinition,
    *,
    author_id: UUID,
    template: RuleTemplateVersion | None,
) -> MonitoringRule | None:
    """Insert the rule row, or return None when another writer just inserted it.

    The insert runs in a savepoint, so losing the race on ``(tenant_id, code)``
    leaves the caller's transaction usable.

    Returns:
        The flushed rule, or None when the code is taken.

    Raises:
        IntegrityError: When any other constraint refuses the row.

    """
    rule = MonitoringRule(
        tenant_id=bound_context(s).require_tenant_id(),
        code=definition.code,
        family=definition.family.value,
        kind=definition.kind.value,
        template_code=None if template is None else template.template_code,
        title=definition.title,
        created_by=author_id,
    )
    try:
        with s.begin_nested():
            s.add(rule)
    except IntegrityError as exc:
        # Only the code's unique constraint means another writer won; a foreign
        # key (an author of another tenant, an unknown template) is an error.
        diag = getattr(exc.orig, "diag", None)
        if getattr(diag, "constraint_name", None) != _RULE_CODE_UNIQUE:
            raise
        return None
    return rule


def _first_version(
    s: Session,
    rule: MonitoringRule,
    definition: RuleDefinition,
    *,
    author_id: UUID,
    template: RuleTemplateVersion | None,
) -> MonitoringRuleVersion:
    return _insert_version(
        s,
        rule,
        number=1,
        definition=definition,
        author_id=author_id,
        template_version_id=None if template is None else template.id,
    )


def _rule_by_code(s: Session, code: str) -> MonitoringRule | None:
    return s.scalars(
        select(MonitoringRule).where(MonitoringRule.code == code)
    ).one_or_none()


def create_rule(
    s: Session, *, template_code: str, body: VersionInput
) -> MonitoringRuleVersion:
    """Create the tenant's rule from a template, or a new version of it.

    When the tenant already has a rule for the template, this adds a version to
    it (plan 4b Deviation 6).

    Returns:
        The new ``DRAFT`` version.

    """
    author_id = _require_writer(s)
    template = template_version(s, template_code)
    existing = _rule_by_code(s, template_code)
    if existing is not None:
        return create_version(s, existing.id, body=body)
    definition = build_definition(
        definition_of(template), body, source=_source(template)
    )
    rule = _new_rule(s, definition, author_id=author_id, template=template)
    if rule is None:
        # Another writer created the rule since the lookup: add a version to it.
        winner = s.scalars(
            select(MonitoringRule.id).where(MonitoringRule.code == template_code)
        ).one()
        return create_version(s, winner, body=body)
    return _first_version(s, rule, definition, author_id=author_id, template=template)


def create_custom_rule(
    s: Session, *, definition: RuleDefinition
) -> MonitoringRuleVersion:
    """Create a rule from a bare kind, without a template (spec §21 Q2).

    Returns:
        Version 1 of the new rule, ``DRAFT``, with no template.

    Raises:
        ValidationFailedError: When custom rules are disabled, the definition is
            not an ALERT rule or its declarations disagree with its parameters.
        ForbiddenError: When the actor is not an ADMIN (``ROLE_FORBIDDEN``).
        DomainError: When the tenant already has a rule with the code.

    """
    if not get_settings().monitoring_allow_custom_rules:
        msg = "custom rules are disabled"
        raise ValidationFailedError(msg)
    ctx = bound_context(s)
    if Role.ADMIN not in ctx.roles:
        msg = "only an admin authors a custom rule"
        raise ForbiddenError(msg, code="ROLE_FORBIDDEN")
    if definition.family is not RuleFamily.ALERT:
        msg = "a custom rule is an ALERT rule"
        raise ValidationFailedError(msg)
    problem = coupling_problem(definition)
    if problem is not None:
        msg = f"rule {definition.code!r}: {problem}"
        raise ValidationFailedError(msg)
    author_id = UUID(ctx.actor_id)
    rule = None
    if _rule_by_code(s, definition.code) is None:
        rule = _new_rule(s, definition, author_id=author_id, template=None)
    if rule is None:
        msg = f"a rule {definition.code} exists"
        raise DomainError(msg)
    return _first_version(s, rule, definition, author_id=author_id, template=None)


def _bounds_of(
    s: Session, version: MonitoringRuleVersion, rule: MonitoringRule
) -> tuple[RuleDefinition, str]:
    # The template version the rule was created from; a custom rule bounds itself.
    if version.template_version_id is None:
        own = RuleDefinition.model_validate(version.definition)
        return own, f"rule {rule.code} v{version.version}"
    template = s.get_one(RuleTemplateVersion, version.template_version_id)
    return definition_of(template), _source(template)


def create_version(
    s: Session, rule_id: UUID, *, body: VersionInput
) -> MonitoringRuleVersion:
    """Add a version to a rule, starting from its latest version.

    Returns:
        The new ``DRAFT`` version, numbered after the latest.

    Raises:
        NotFoundError: When the tenant has no such rule.

    """
    author_id = _require_writer(s)
    rule = s.scalars(
        select(MonitoringRule).where(MonitoringRule.id == rule_id).with_for_update()
    ).one_or_none()
    if rule is None:
        msg = "rule not found"
        raise NotFoundError(msg)
    latest = s.scalars(
        select(MonitoringRuleVersion)
        .where(MonitoringRuleVersion.rule_id == rule.id)
        .order_by(MonitoringRuleVersion.version.desc())
        .limit(1)
    ).one()
    bounds, source = _bounds_of(s, latest, rule)
    base = RuleDefinition.model_validate(latest.definition)
    definition = build_definition(bounds, _merged(base, body), source=source)
    return _insert_version(
        s,
        rule,
        number=latest.version + 1,
        definition=definition,
        author_id=author_id,
        template_version_id=latest.template_version_id,
    )


def _changed(old: RuleDefinition, new: RuleDefinition) -> list[str]:
    fields = ("params", "segment", "severity", "dedup")
    return [name for name in fields if getattr(old, name) != getattr(new, name)]


def update_version(
    s: Session, version_id: UUID, *, body: VersionInput
) -> MonitoringRuleVersion:
    """Edit a ``DRAFT`` or ``SIMULATED`` version in place.

    Any change sends it back to ``DRAFT`` and clears its simulation, since the
    definition hash it was simulated with no longer holds.

    Returns:
        The version, changed or not.

    Raises:
        NotFoundError: When the tenant has no such version.
        RuleTransitionNotAllowedError: When the version is past review
            (``STATE``).

    """
    _ = _require_writer(s)
    version = s.scalars(
        select(MonitoringRuleVersion)
        .where(MonitoringRuleVersion.id == version_id)
        .with_for_update()
    ).one_or_none()
    if version is None:
        msg = "rule version not found"
        raise NotFoundError(msg)
    if version.status not in _EDITABLE:
        raise RuleTransitionNotAllowedError([RuleBlockedReason.STATE])
    rule = s.get_one(MonitoringRule, (version.tenant_id, version.rule_id))
    bounds, source = _bounds_of(s, version, rule)
    old = RuleDefinition.model_validate(version.definition)
    new = build_definition(bounds, _merged(old, body), source=source)
    changed = _changed(old, new)
    if not changed:
        return version
    version.definition = new.model_dump(mode="json")
    version.definition_hash = new.definition_hash()
    version.sync = new.sync
    version.status = RuleVersionStatus.DRAFT
    version.simulation_id = None
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="rules.version_updated",
            entity_type="monitoring_rule_version",
            entity_id=str(version.id),
            detail={"changed": list[JsonValue](changed)},
        ),
    )
    return version


def _tenant_packs(s: Session) -> set[str]:
    # Packs assigned today or from a later date.
    today = policy_date(clock.now())
    query = (
        select(PolicyPack.code)
        .join(PolicyPackVersion, PolicyPackVersion.pack_id == PolicyPack.id)
        .join(
            TenantPackAssignment,
            TenantPackAssignment.pack_version_id == PolicyPackVersion.id,
        )
        .where(
            or_(
                TenantPackAssignment.effective_to.is_(None),
                TenantPackAssignment.effective_to >= today,
            )
        )
        .group_by(PolicyPack.code)
    )
    return set(s.scalars(query))


def seed_tenant_rules(s: Session, *, author_id: UUID) -> list[MonitoringRuleVersion]:
    """Give the tenant a draft of every template its packs carry and it lacks.

    The packs are those of the tenant's assignments in force today or later; a
    template applies when its packs intersect them. Codes the tenant already has a
    rule for are skipped, so seeding twice adds nothing.

    Returns:
        The draft versions created, by template code.

    """
    have = set(s.scalars(select(MonitoringRule.code)))
    created: list[MonitoringRuleVersion] = []
    codes: list[str] = []
    for template in latest_template_versions(s, packs=_tenant_packs(s)):
        if template.template_code in have:
            continue
        definition = build_definition(
            definition_of(template), VersionInput(), source=_source(template)
        )
        rule = _new_rule(s, definition, author_id=author_id, template=template)
        if rule is None:
            # A concurrent seed or create got there first.
            continue
        created.append(
            _first_version(s, rule, definition, author_id=author_id, template=template)
        )
        codes.append(template.template_code)
    if created:
        _ = record(
            s,
            AuditEvent(
                action="rules.seeded",
                entity_type="monitoring_rule",
                detail={"codes": list[JsonValue](codes)},
            ),
        )
    return created
