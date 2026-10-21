"""Tenant rules and versions within the template's bounds (spec §9.4, plan 4b B5)."""

from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from tests.helpers import audit_events, new_tenant, platform_ctx, staff_session
from tests.platform.policy_support import install_both_packs
from xolotl.modules.monitoring.errors import (
    RuleParamOutOfBoundsError,
    RuleTemplateUnknownError,
    RuleTransitionNotAllowedError,
)
from xolotl.modules.monitoring.rules import versions
from xolotl.modules.monitoring.rules.definitions import (
    Dedup,
    RuleDefinition,
    SegmentFilter,
    Severity,
)
from xolotl.modules.monitoring.rules.enums import (
    RuleVersionStatus,
    SimulationStatus,
)
from xolotl.modules.monitoring.rules.library import read_library
from xolotl.modules.monitoring.rules.models import (
    MonitoringRule,
    MonitoringRuleVersion,
    RuleSimulation,
)
from xolotl.modules.monitoring.rules.templates import (
    definition_of,
    load_templates,
    template_version,
)
from xolotl.modules.monitoring.rules.versions import (
    BoundViolation,
    VersionInput,
    build_definition,
    check_params,
    coupling_problem,
    create_custom_rule,
    create_rule,
    create_version,
    seed_tenant_rules,
    update_version,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.errors import (
    DomainError,
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
)
from xolotl.platform.policy import assign_pack
from xolotl.platform.policy.models import TenantPackAssignment
from xolotl.platform.tenancy.context import TenantContext

ITF = "MX/ITF-IFPE"
AVI = "MX/LFPIORPI-AVI"
CASH = "CASH_STRUCTURING"
SOURCE = "template CASH_STRUCTURING v1"


@pytest.fixture
def library(admin_engine: Engine) -> None:
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        _ = load_templates(s, read_library(), loaded_by="cli:test")


def _cash_template() -> RuleDefinition:
    with tenant_session(platform_ctx()) as s:
        return definition_of(template_version(s, CASH))


def _create(
    tenant: CreatedTenant,
    code: str = CASH,
    body: VersionInput | None = None,
    roles: Iterable[Role] = (Role.SENIOR_ANALYST,),
) -> MonitoringRuleVersion:
    with staff_session(tenant.tenant_id, tenant.admin_user_id, roles) as s:
        return create_rule(s, template_code=code, body=body or VersionInput())


def _params(**values: Any) -> VersionInput:
    return VersionInput(params=values)


def _band(version: MonitoringRuleVersion) -> list[tuple[str, str, str]]:
    definition = RuleDefinition.model_validate(version.definition)
    return [
        (
            str(declaration.where.amount_gte),
            str(declaration.where.amount_lt),
            declaration.window,
        )
        for declaration in definition.aggregates
    ]


def _actions(tenant_id: UUID) -> list[str]:
    return [event.action for event in audit_events(tenant_id)]


@pytest.mark.usefixtures("library")
def test_a_rule_is_created_from_a_template_with_default_params(
    tenant: CreatedTenant,
):
    version = _create(tenant)
    assert (version.version, version.status) == (1, RuleVersionStatus.DRAFT)
    expected = build_definition(_cash_template(), VersionInput(), source=SOURCE)
    assert version.definition_hash == expected.definition_hash()
    assert version.definition == expected.model_dump(mode="json")
    assert version.author_id == tenant.admin_user_id
    assert version.sync is False
    with tenant_session(platform_ctx()) as s:
        assert version.template_version_id == template_version(s, CASH).id
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        rule = s.get_one(MonitoringRule, (tenant.tenant_id, version.rule_id))
        assert (rule.code, rule.template_code, rule.family, rule.kind) == (
            CASH,
            CASH,
            "ALERT",
            "STRUCTURING",
        )
        assert rule.created_by == tenant.admin_user_id
    [event] = [e for e in audit_events(tenant.tenant_id) if e.action.startswith("rul")]
    assert event.action == "rules.version_created"
    assert event.entity_id == str(version.id)
    assert event.detail == {
        "rule_id": str(version.rule_id),
        "rule_code": CASH,
        "version": 1,
    }


@pytest.mark.usefixtures("library")
def test_an_unknown_template_is_404(tenant: CreatedTenant):
    with pytest.raises(RuleTemplateUnknownError) as caught:
        _ = _create(tenant, "NOPE")
    assert caught.value.status == 404


@pytest.mark.usefixtures("library")
def test_params_outside_the_bounds_are_refused_with_the_source(tenant: CreatedTenant):
    with pytest.raises(RuleParamOutOfBoundsError) as caught:
        _ = _create(tenant, body=_params(threshold=900))
    assert caught.value.status == 422
    [item] = caught.value.errors
    assert item.loc == ("params", "threshold")
    assert item.type == "RULE_PARAM_OUT_OF_BOUNDS"
    assert "min 1000" in item.msg
    assert SOURCE in item.msg
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        assert s.scalars(select(MonitoringRule)).all() == []


def test_tighten_lower_refuses_a_looser_threshold():
    template = _cash_template_from_file()
    [violation] = check_params(template, {"threshold": 6000}, source=SOURCE)
    assert violation == BoundViolation(
        "threshold", "tighten", "LOWER from 5000", SOURCE
    )
    with pytest.raises(RuleParamOutOfBoundsError) as caught:
        _ = build_definition(template, _params(threshold="6000"), source=SOURCE)
    assert caught.value.code == "RULE_PARAM_OUT_OF_BOUNDS"
    assert "tighten" in caught.value.errors[0].msg
    assert check_params(template, {"threshold": "4000"}, source=SOURCE) == []


def test_every_bound_is_checked():
    template = _cash_template_from_file()
    found = check_params(
        template,
        {
            "near_ratio": Decimal("0.96"),
            "min_count": 1,
            "window": 7,
            "nope": 1,
            "threshold": "lots",
        },
        source=SOURCE,
    )
    assert [(v.param, v.bound, v.limit) for v in found] == [
        ("near_ratio", "max", "0.95"),
        ("min_count", "min", "2"),
        ("window", "type", "text"),
        ("nope", "type", "no such parameter"),
        ("threshold", "type", "decimal"),
    ]
    assert check_params(template, {"min_count": True}, source=SOURCE)[0].bound == (
        "type"
    )


def _cash_template_from_file() -> RuleDefinition:
    return next(file.definition for file in read_library() if file.code == CASH)


def _template_from_file(code: str) -> RuleDefinition:
    return next(file.definition for file in read_library() if file.code == code)


def test_a_choice_param_takes_only_its_choices():
    template = _template_from_file("TIER_N1_MONTHLY_OPS")
    source = "template TIER_N1_MONTHLY_OPS v1"
    with pytest.raises(RuleParamOutOfBoundsError) as caught:
        _ = build_definition(template, _params(outcome="ALLOW"), source=source)
    assert caught.value.errors[0].loc == ("params", "outcome")
    assert "choices" in caught.value.errors[0].msg
    review = build_definition(template, _params(outcome="REVIEW"), source=source)
    assert review.params["outcome"].value == "REVIEW"


def test_the_derived_declarations_follow_the_tuned_params():
    template = _cash_template_from_file()
    tuned = build_definition(
        template,
        _params(threshold="4000", near_ratio="0.8", window="P10D"),
        source=SOURCE,
    )
    for declaration in tuned.aggregates:
        assert declaration.where.amount_gte == Decimal(3200)
        assert declaration.where.amount_lt == Decimal(4000)
        assert declaration.window == "P10D"
    assert tuned.evidence.transactions_window == "P10D"
    assert tuned.evidence.transactions_where.amount_gte == Decimal(3200)
    assert {d.agg_id() for d in tuned.aggregates}.isdisjoint(
        d.agg_id() for d in template.aggregates
    )
    assert coupling_problem(tuned) is None
    velocity = build_definition(
        _template_from_file("NEW_ACCOUNT_VELOCITY"),
        _params(window="PT6H"),
        source="template NEW_ACCOUNT_VELOCITY v1",
    )
    assert {d.window for d in velocity.aggregates} == {"PT6H"}
    assert velocity.evidence.transactions_window == "PT6H"
    dormant = build_definition(
        _template_from_file("DORMANT_REACTIVATION"),
        _params(window="P3D"),
        source="template DORMANT_REACTIVATION v1",
    )
    assert {d.name: d.window for d in dormant.aggregates} == {
        "window_sum": "P3D",
        "last_seen": "P6M",
    }


def test_the_shipped_defaults_build_to_themselves():
    for file in read_library():
        built = build_definition(
            file.definition, VersionInput(), source=f"template {file.code} v1"
        )
        assert built.definition_hash() == file.definition_hash, file.code
        assert coupling_problem(file.definition) is None


@pytest.mark.parametrize(
    ("window", "bound", "limit"),
    [
        ("P30D", "max", "P7D (VELOCITY window)"),
        ("P7M", "max", "P6M"),
        ("PT0H", "min", "PT1H"),
        ("P1M2D", "type", "months or days, not both"),
        ("soon", "type", "an ISO duration such as P1D"),
    ],
)
def test_a_window_outside_its_kinds_limits_is_out_of_bounds(window, bound, limit):
    source = "template NEW_ACCOUNT_VELOCITY v1"
    template = _template_from_file("NEW_ACCOUNT_VELOCITY")
    [violation] = check_params(template, {"window": window}, source=source)
    assert violation == BoundViolation("window", bound, limit, source)
    with pytest.raises(RuleParamOutOfBoundsError) as caught:
        _ = build_definition(template, _params(window=window), source=source)
    assert caught.value.errors[0].loc == ("params", "window")


def test_a_tuned_definition_that_is_invalid_as_a_whole_is_refused():
    with pytest.raises(ValidationFailedError, match="calendar MONTH") as caught:
        _ = build_definition(
            _template_from_file("TIER_N1_MONTHLY_OPS"),
            VersionInput(dedup=Dedup(window="P1D")),
            source="template TIER_N1_MONTHLY_OPS v1",
        )
    assert caught.value.code == "VALIDATION_FAILED"


def test_a_structuring_window_may_run_past_seven_days():
    template = _cash_template_from_file()
    assert check_params(template, {"window": "P30D"}, source=SOURCE) == []


def test_tighten_raise_refuses_a_lower_value():
    data = _cash_template_from_file().model_dump()
    data["params"]["min_count"] = {"value": 3, "min": 2, "tighten": "RAISE"}
    template = RuleDefinition.model_validate(data)
    [violation] = check_params(template, {"min_count": 2}, source=SOURCE)
    assert violation == BoundViolation("min_count", "tighten", "RAISE from 3", SOURCE)
    assert check_params(template, {"min_count": 4}, source=SOURCE) == []
    assert check_params(template, {"min_count": 3}, source=SOURCE) == []


def test_a_float_is_refused_for_a_parameter():
    with pytest.raises(ValidationError, match="never a float"):
        _ = VersionInput.model_validate({"params": {"threshold": 4000.5}})
    body = VersionInput.model_validate({"params": {"threshold": "4000.5"}})
    assert body.params == {"threshold": "4000.5"}
    assert VersionInput.model_validate({"params": {"min_count": 4}}).params == {
        "min_count": 4
    }


@pytest.mark.usefixtures("library")
def test_a_first_create_that_loses_the_race_becomes_version_2(
    tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    first = _create(tenant)

    # The second writer read "no rule" before the first one committed.
    def nobody(_s: object, _code: str) -> None:
        return None

    monkeypatch.setattr(versions, "_rule_by_code", nobody)
    second = _create(tenant, body=_params(near_ratio="0.8"))
    assert (second.rule_id, second.version) == (first.rule_id, 2)


@pytest.mark.usefixtures("library")
def test_a_second_create_for_the_same_template_is_a_new_version(tenant: CreatedTenant):
    first = _create(tenant)
    second = _create(tenant, body=_params(near_ratio="0.8"))
    assert (second.rule_id, second.version) == (first.rule_id, 2)
    assert second.status == RuleVersionStatus.DRAFT
    assert second.template_version_id == first.template_version_id
    assert _band(second) == [("4000", "5000", "P7D"), ("4000", "5000", "P7D")]
    # The next version starts from the latest one, not from the template.
    third = _create(tenant, body=VersionInput(severity=Severity.HIGH))
    assert third.version == 3
    definition = RuleDefinition.model_validate(third.definition)
    assert definition.params["near_ratio"].value == Decimal("0.8")
    assert definition.severity is Severity.HIGH
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        assert len(s.scalars(select(MonitoringRule)).all()) == 1
        with pytest.raises(NotFoundError):
            _ = create_version(s, UUID(int=7), body=VersionInput())


def _simulated(tenant: CreatedTenant, version: MonitoringRuleVersion) -> UUID:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        simulation = RuleSimulation(
            tenant_id=tenant.tenant_id,
            rule_version_id=version.id,
            requested_by=tenant.admin_user_id,
            range_start=date(2026, 6, 1),
            range_end=date(2026, 12, 1),
            status=SimulationStatus.COMPLETED,
            definition_hash=version.definition_hash,
        )
        s.add(simulation)
        s.flush()
        _ = s.execute(
            update(MonitoringRuleVersion)
            .where(MonitoringRuleVersion.id == version.id)
            .values(status=RuleVersionStatus.SIMULATED, simulation_id=simulation.id)
        )
        return simulation.id


@pytest.mark.usefixtures("library")
def test_updating_a_simulated_version_resets_it_to_draft(tenant: CreatedTenant):
    version = _create(tenant)
    _ = _simulated(tenant, version)
    roles = (Role.MLRO,)
    with staff_session(tenant.tenant_id, tenant.admin_user_id, roles) as s:
        same = update_version(s, version.id, body=_params(near_ratio="0.70"))
        assert same.status == RuleVersionStatus.SIMULATED
        assert same.simulation_id is not None
    with staff_session(tenant.tenant_id, tenant.admin_user_id, roles) as s:
        body = VersionInput(
            params={"near_ratio": "0.8"},
            segment=SegmentFilter(tier=["N2"]),
            dedup=Dedup(window="P7D", cooldown="P14D"),
        )
        updated = update_version(s, version.id, body=body)
        assert updated.status == RuleVersionStatus.DRAFT
        assert updated.simulation_id is None
        assert updated.definition_hash != version.definition_hash
        definition = RuleDefinition.model_validate(updated.definition)
        assert updated.definition_hash == definition.definition_hash()
        assert definition.segment == SegmentFilter(tier=["N2"])
        assert definition.dedup.cooldown == "P14D"
    events = [e for e in audit_events(tenant.tenant_id) if e.action.startswith("rul")]
    assert [e.action for e in events] == [
        "rules.version_created",
        "rules.version_updated",
    ]
    assert events[-1].detail == {"changed": ["params", "segment", "dedup"]}
    assert events[-1].entity_id == str(version.id)


@pytest.mark.usefixtures("library")
def test_a_version_in_review_cannot_be_edited(tenant: CreatedTenant):
    version = _create(tenant)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = s.execute(
            update(MonitoringRuleVersion)
            .where(MonitoringRuleVersion.id == version.id)
            .values(status=RuleVersionStatus.IN_REVIEW)
        )
    with (
        pytest.raises(RuleTransitionNotAllowedError) as caught,
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = update_version(s, version.id, body=_params(near_ratio="0.8"))
    assert caught.value.status == 409
    assert caught.value.code == "RULE_TRANSITION_NOT_ALLOWED"
    assert caught.value.blocked_reasons == ("STATE",)
    with (
        pytest.raises(NotFoundError),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = update_version(s, UUID(int=9), body=VersionInput())


@pytest.mark.usefixtures("library")
@pytest.mark.parametrize("role", [Role.ANALYST, Role.AUDITOR_RO])
def test_only_writers_create_rules(tenant: CreatedTenant, role: Role):
    with pytest.raises(ForbiddenError) as caught:
        _ = _create(tenant, roles=(role,))
    assert caught.value.code == "ROLE_FORBIDDEN"
    version = _create(tenant, roles=(Role.MLRO,))
    with (
        pytest.raises(ForbiddenError),
        staff_session(tenant.tenant_id, tenant.admin_user_id, (role,)) as s,
    ):
        _ = update_version(s, version.id, body=VersionInput())


def _allow_custom(monkeypatch: pytest.MonkeyPatch, *, allowed: bool) -> None:
    settings = get_settings().model_copy(
        update={"monitoring_allow_custom_rules": allowed}
    )
    monkeypatch.setattr(versions, "get_settings", lambda: settings)


def _custom(**changes: Any) -> RuleDefinition:
    data = _template_from_file("SELF_CUSTODY_OUTFLOW").model_dump()
    threshold = {"value": "1000", "tighten": "LOWER", "unit": "USD"}
    data.update({"code": "MY_SELF_CUSTODY", "params": {"threshold": threshold}})
    data.update(changes)
    return RuleDefinition.model_validate(data)


@pytest.mark.usefixtures("library")
def test_custom_rules_are_off_by_default_and_admin_only(
    tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    def custom(definition: RuleDefinition, role: Role) -> MonitoringRuleVersion:
        with staff_session(tenant.tenant_id, tenant.admin_user_id, (role,)) as s:
            return create_custom_rule(s, definition=definition)

    with pytest.raises(ValidationFailedError) as off:
        _ = custom(_custom(), Role.ADMIN)
    assert (off.value.code, off.value.detail) == (
        "VALIDATION_FAILED",
        "custom rules are disabled",
    )
    _allow_custom(monkeypatch, allowed=True)
    with pytest.raises(ForbiddenError) as forbidden:
        _ = custom(_custom(), Role.SENIOR_ANALYST)
    assert forbidden.value.code == "ROLE_FORBIDDEN"
    version = custom(_custom(), Role.ADMIN)
    assert version.status == RuleVersionStatus.DRAFT
    assert version.template_version_id is None
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        rule = s.get_one(MonitoringRule, (tenant.tenant_id, version.rule_id))
        assert (rule.code, rule.template_code) == ("MY_SELF_CUSTODY", None)
    with pytest.raises(DomainError, match="a rule MY_SELF_CUSTODY exists"):
        _ = custom(_custom(), Role.ADMIN)
    # A custom rule bounds itself: its own params' bounds and defaults.
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        second = create_version(s, version.rule_id, body=_params(threshold="900"))
        assert second.version == 2
        with pytest.raises(RuleParamOutOfBoundsError) as caught:
            _ = create_version(s, version.rule_id, body=_params(threshold="5000"))
        assert "rule MY_SELF_CUSTODY v2" in caught.value.errors[0].msg


@pytest.mark.usefixtures("library")
def test_a_custom_rule_must_follow_its_params(
    tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    _allow_custom(monkeypatch, allowed=True)
    data = _cash_template_from_file().model_dump()
    data["code"] = "MY_CASH"
    data["params"]["near_ratio"]["value"] = Decimal("0.8")
    loose = RuleDefinition.model_validate(data)
    with (
        pytest.raises(ValidationFailedError, match="band or windows disagree"),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = create_custom_rule(s, definition=loose)


def _assign(tenant: CreatedTenant, assignments: list[tuple[UUID, str | None]]) -> None:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        for version_id, product in assignments:
            _ = assign_pack(
                s,
                pack_version_id=version_id,
                product=product,
                effective_from=date(2026, 1, 1),
                approved_by=tenant.admin_user_id,
            )


def _seed(tenant: CreatedTenant) -> list[MonitoringRuleVersion]:
    ctx = TenantContext.platform("cli:seed", tenant_id=tenant.tenant_id)
    with tenant_session(ctx) as s:
        return seed_tenant_rules(s, author_id=tenant.admin_user_id)


@pytest.mark.usefixtures("library")
def test_seeding_follows_the_tenants_packs(tenant: CreatedTenant, admin_engine: Engine):
    packs = install_both_packs(admin_engine)
    _assign(tenant, [(packs[ITF], "EMONEY"), (packs[AVI], "CRYPTO")])
    itf_only = new_tenant("itf", "admin@itf.mx")
    _assign(itf_only, [(packs[ITF], None)])
    avi_only = new_tenant("avi", "admin@avi.mx")
    _assign(avi_only, [(packs[AVI], None)])
    bare = new_tenant("bare", "admin@bare.mx")

    seeded = _seed(tenant)
    assert len(seeded) == 16
    assert all(row.status == RuleVersionStatus.DRAFT for row in seeded)
    assert all(row.author_id == tenant.admin_user_id for row in seeded)
    assert len(_seed(itf_only)) == 16
    avi_seeded = _seed(avi_only)
    assert len(avi_seeded) == 7
    assert _seed(bare) == []
    assert _seed(tenant) == []
    [seed_event] = [
        e for e in audit_events(avi_only.tenant_id) if e.action == "rules.seeded"
    ]
    assert seed_event.detail == {
        "codes": [
            "BLOCKED_PERSON_PARTY",
            "COUNTERPARTY_TRUE_MATCH",
            "CRYPTO_OFF_RAMP",
            "NEW_ACCOUNT_VELOCITY",
            "PEP_LARGE_OPERATION",
            "RAPID_PASS_THROUGH",
            "SELF_CUSTODY_OUTFLOW",
        ]
    }
    assert _actions(avi_only.tenant_id).count("rules.version_created") == 7
    assert "rules.seeded" not in _actions(bare.tenant_id)


TODAY = datetime(2026, 10, 21, 18, tzinfo=UTC)


@pytest.mark.usefixtures("library")
def test_seeding_reads_assignments_in_force_today_or_later(
    admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(clock, "now", lambda: TODAY)
    avi = install_both_packs(admin_engine)[AVI]

    def tenant_with(slug: str, *, start: date, end: date | None) -> CreatedTenant:
        created = new_tenant(slug, f"admin@{slug}.mx")
        with staff_session(created.tenant_id, created.admin_user_id) as s:
            _ = assign_pack(
                s,
                pack_version_id=avi,
                product=None,
                effective_from=start,
                approved_by=created.admin_user_id,
            )
            _ = s.execute(update(TenantPackAssignment).values(effective_to=end))
        return created

    ended = tenant_with("ended", start=date(2026, 1, 1), end=date(2026, 10, 20))
    ending = tenant_with("ending", start=date(2026, 1, 1), end=date(2026, 10, 21))
    coming = tenant_with("coming", start=date(2026, 11, 1), end=None)
    assert _seed(ended) == []
    assert len(_seed(ending)) == 7
    assert len(_seed(coming)) == 7


@pytest.mark.usefixtures("library")
def test_a_foreign_author_is_an_error_not_a_lost_race(
    tenant: CreatedTenant, admin_engine: Engine
):
    packs = install_both_packs(admin_engine)
    _assign(tenant, [(packs[ITF], None)])
    other = new_tenant("other", "admin@other.mx")
    ctx = TenantContext.platform("cli:seed", tenant_id=tenant.tenant_id)
    with (
        pytest.raises(IntegrityError, match="created_by"),
        tenant_session(ctx) as s,
    ):
        _ = seed_tenant_rules(s, author_id=other.admin_user_id)
    with (
        pytest.raises(IntegrityError, match="created_by"),
        staff_session(tenant.tenant_id, other.admin_user_id, (Role.MLRO,)) as s,
    ):
        _ = create_rule(s, template_code=CASH, body=VersionInput())


@pytest.mark.usefixtures("library")
def test_a_custom_rule_that_loses_the_race_is_a_conflict(
    tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    _allow_custom(monkeypatch, allowed=True)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = create_custom_rule(s, definition=_custom())

    def nobody(_s: object, _code: str) -> None:
        return None

    monkeypatch.setattr(versions, "_rule_by_code", nobody)
    with (
        pytest.raises(DomainError, match="a rule MY_SELF_CUSTODY exists"),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = create_custom_rule(s, definition=_custom())
