"""Rule assignments by date and product, and the active rule set (plan 4b B6)."""

import hashlib
from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, update

from tests.helpers import new_tenant, staff_session
from tests.modules.monitoring.rules.conftest import lists, rule
from tests.modules.monitoring.rules.lifecycle_support import (
    APPROVER_ROLES,
    activated,
    add_staff,
    freeze,
    load_library,
)
from xolotl.modules.monitoring.rules import assignments, lifecycle
from xolotl.modules.monitoring.rules.assignments import (
    LoadedRule,
    RuleSet,
    active_rules,
    assignments_on,
    clear_rule_cache,
)
from xolotl.modules.monitoring.rules.definitions import RuleDefinition, RuleKind
from xolotl.modules.monitoring.rules.lifecycle import retire_version
from xolotl.modules.monitoring.rules.models import RuleAssignment
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.config import get_settings
from xolotl.platform.enums import Role
from xolotl.platform.jobs.queue import PostgresJobQueue

CASH = "CASH_STRUCTURING"
BORDER = "BORDER_ZONE_CASH"


pytestmark = pytest.mark.usefixtures("frozen")


@pytest.fixture
def library(admin_engine: Engine) -> None:
    load_library(admin_engine)


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze(monkeypatch)
    clear_rule_cache()


@pytest.fixture
def approver(tenant: CreatedTenant) -> UUID:
    return add_staff(tenant, Role.MLRO)


def _codes(tenant: CreatedTenant, on: date, product: str | None) -> list[str]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id, APPROVER_ROLES) as s:
        return [r.rule_code for r in active_rules(s, on=on, product=product).rules]


@pytest.mark.usefixtures("library")
def test_rules_resolve_by_date_and_product_like_packs(
    tenant: CreatedTenant, approver: UUID, queue: PostgresJobQueue
):
    _ = activated(tenant, approver, queue, CASH, effective_from=date(2026, 11, 1))
    _ = activated(
        tenant,
        approver,
        queue,
        BORDER,
        effective_from=date(2026, 11, 15),
        product="EMONEY",
    )
    assert _codes(tenant, date(2026, 10, 31), None) == []
    assert _codes(tenant, date(2026, 11, 1), None) == [CASH]
    assert _codes(tenant, date(2026, 11, 20), None) == [CASH]
    assert _codes(tenant, date(2026, 11, 14), "EMONEY") == [CASH]
    assert _codes(tenant, date(2026, 11, 20), "EMONEY") == [BORDER, CASH]
    assert _codes(tenant, date(2026, 11, 20), "CRYPTO") == [CASH]
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        rows = assignments_on(s, on=date(2026, 11, 20), product="EMONEY")
        assert sorted(row.product or "" for row in rows) == ["", "EMONEY"]


@pytest.mark.usefixtures("library")
def test_a_retired_version_stops_applying_after_today(
    tenant: CreatedTenant, approver: UUID, queue: PostgresJobQueue
):
    version = activated(tenant, approver, queue, CASH)
    with staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s:
        _ = retire_version(s, version.id)
    assert _codes(tenant, date(2026, 10, 30), None) == [CASH]
    assert _codes(tenant, date(2026, 10, 31), None) == []


@pytest.mark.usefixtures("library")
def test_a_loaded_rule_carries_its_version_and_assignment(
    tenant: CreatedTenant, approver: UUID, queue: PostgresJobQueue
):
    version = activated(tenant, approver, queue, CASH)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        ruleset = active_rules(s, on=date(2026, 10, 30), product="EMONEY")
        [assignment] = assignments_on(s, on=date(2026, 10, 30), product=None)
    [loaded] = ruleset.rules
    definition = RuleDefinition.model_validate(version.definition)
    assert (ruleset.on, ruleset.product) == (date(2026, 10, 30), "EMONEY")
    assert loaded.rule_id == version.rule_id
    assert loaded.rule_version_id == version.id
    assert loaded.assignment_id == assignment.id
    assert loaded.definition_hash == version.definition_hash
    assert loaded.sync is version.sync
    assert loaded.segment == definition.segment
    assert loaded.typology == tuple(definition.typology)
    assert ruleset.lists.content_hash == lists().content_hash


@pytest.mark.usefixtures("library")
def test_list_names_are_resolved_at_load(
    tenant: CreatedTenant, approver: UUID, queue: PostgresJobQueue
):
    _ = activated(tenant, approver, queue, BORDER)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        [loaded] = active_rules(s, on=date(2026, 10, 30), product=None).rules
    border = set(lists().regions["BORDER_ZONE_MX"])
    assert set(loaded.definition.on.geo_admin_area_in or ()) == border
    assert set(
        loaded.definition.evidence.transactions_where.geo_admin_area_in or ()
    ) == (border)
    for declaration in loaded.definition.aggregates:
        assert set(declaration.where.geo_admin_area_in or ()) == border
        assert declaration.lists_hash == lists().content_hash.hex()


def _loaded(definition: RuleDefinition, *, sync: bool = False) -> LoadedRule:
    return LoadedRule(
        rule_id=uuid4(),
        rule_version_id=uuid4(),
        rule_code=definition.code,
        assignment_id=uuid4(),
        definition=definition,
        segment=definition.segment,
        sync=sync,
        definition_hash=definition.definition_hash(),
        typology=tuple(definition.typology),
    )


def _ruleset(*rules: LoadedRule) -> RuleSet:
    return RuleSet(on=date(2026, 12, 10), product=None, rules=rules, lists=lists())


def test_the_rule_set_deduplicates_declarations_by_agg_id():
    structuring = rule(RuleKind.STRUCTURING)
    twin = structuring.model_copy(update={"code": "TWIN_STRUCTURING"})
    velocity = rule(RuleKind.VELOCITY)
    ruleset = _ruleset(_loaded(structuring), _loaded(twin), _loaded(velocity))
    ids = [d.agg_id() for d in ruleset.declarations]
    assert len(ids) == len(set(ids))
    assert set(ids) == {
        d.agg_id() for d in (*structuring.aggregates, *velocity.aggregates)
    }
    assert len(ids) == len(structuring.aggregates) + len(velocity.aggregates)


def test_the_ruleset_hash_is_order_independent_and_covers_the_sync_rules_only():
    a = _loaded(rule(RuleKind.STRUCTURING), sync=True)
    b = _loaded(rule(RuleKind.VELOCITY), sync=True)
    c = _loaded(rule(RuleKind.PROFILE_DEVIATION), sync=False)
    assert _ruleset(a, b, c).sync_rules == (a, b)
    assert _ruleset(a, b).ruleset_hash() == _ruleset(b, a).ruleset_hash()
    assert _ruleset(a, b, c).ruleset_hash() == _ruleset(a, b).ruleset_hash()
    assert _ruleset(a).ruleset_hash() != _ruleset(a, b).ruleset_hash()
    expected = hashlib.sha256(
        b"".join(sorted([a.definition_hash, b.definition_hash]))
    ).digest()
    assert _ruleset(a, b).ruleset_hash() == expected


class _Ticks:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.usefixtures("library")
def test_the_cache_refreshes_on_activation_and_after_the_ttl(
    tenant: CreatedTenant,
    approver: UUID,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
):
    ticks = _Ticks()
    monkeypatch.setattr(assignments, "monotonic", ticks)
    version = activated(tenant, approver, queue, CASH)
    on = date(2026, 10, 30)
    assert _codes(tenant, on, None) == [CASH]
    # A change that no activation stamps is not seen until the entry expires.
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = s.execute(
            update(RuleAssignment)
            .where(RuleAssignment.rule_version_id == version.id)
            .values(product="CRYPTO")
        )
    ticks.now += 29
    assert _codes(tenant, on, None) == [CASH]
    ticks.now += 2
    assert _codes(tenant, on, None) == []
    # An activation is seen at once.
    _ = activated(tenant, approver, queue, BORDER)
    assert _codes(tenant, on, None) == [BORDER]


@pytest.mark.usefixtures("library")
def test_a_zero_ttl_reads_the_rules_every_time(
    tenant: CreatedTenant,
    approver: UUID,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
):
    settings = get_settings().model_copy(update={"monitoring_rule_cache_seconds": 0})
    monkeypatch.setattr(assignments, "get_settings", lambda: settings)
    version = activated(tenant, approver, queue, CASH)
    on = date(2026, 10, 30)
    assert _codes(tenant, on, None) == [CASH]
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = s.execute(
            update(RuleAssignment)
            .where(RuleAssignment.rule_version_id == version.id)
            .values(product="CRYPTO")
        )
    assert _codes(tenant, on, None) == []


@pytest.mark.usefixtures("library")
def test_another_tenant_sees_none_of_the_rules(
    tenant: CreatedTenant, approver: UUID, queue: PostgresJobQueue
):
    _ = activated(tenant, approver, queue, CASH)
    other = new_tenant("otra", "admin@otra.mx")
    with staff_session(other.tenant_id, other.admin_user_id) as s:
        assert active_rules(s, on=date(2026, 10, 30), product=None).rules == ()
        assert assignments_on(s, on=date(2026, 10, 30), product=None) == []


@pytest.mark.usefixtures("library")
def test_another_workers_activation_is_seen_before_the_ttl(
    tenant: CreatedTenant,
    approver: UUID,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
):
    # Another worker's activation never clears this process's cache: the key's
    # activation stamps must change instead.
    ticks = _Ticks()
    monkeypatch.setattr(assignments, "monotonic", ticks)
    monkeypatch.setattr(lifecycle, "clear_rule_cache", lambda: None)
    _ = activated(tenant, approver, queue, CASH)
    on = date(2026, 10, 30)
    assert _codes(tenant, on, None) == [CASH]
    _ = activated(tenant, approver, queue, BORDER)
    assert _codes(tenant, on, None) == [BORDER, CASH]
    version = activated(tenant, approver, queue, BORDER)
    with staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s:
        _ = retire_version(s, version.id)
    assert _codes(tenant, date(2026, 10, 31), None) == [CASH]
