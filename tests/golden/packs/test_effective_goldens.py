"""The effective policy per date and product matches ``effective.yaml``."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine

from tests.helpers import new_tenant, staff_session
from tests.platform.policy_support import install_both_packs, load_golden
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.errors import NotFoundError
from xolotl.platform.policy import (
    EffectivePolicy,
    Quantity,
    TenantOverride,
    assign_pack,
    create_override,
    effective_policy,
)

GOLDEN = load_golden()


@pytest.fixture
def installed(admin_engine: Engine) -> dict[str, UUID]:
    return install_both_packs(admin_engine)


@pytest.fixture
def assigned(engine: Engine, installed: dict[str, UUID]) -> CreatedTenant:
    tenant = new_tenant()
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        for row in GOLDEN["assignments"]:
            _ = assign_pack(
                s,
                pack_version_id=installed[row["pack"]],
                product=row["product"],
                effective_from=row["effective_from"],
                approved_by=tenant.admin_user_id,
            )
    return tenant


@pytest.fixture
def overridden(
    assigned: CreatedTenant, monkeypatch: pytest.MonkeyPatch
) -> CreatedTenant:
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 2, 1, 12, tzinfo=UTC))
    with staff_session(assigned.tenant_id, assigned.admin_user_id) as s:
        _ = create_override(
            s,
            TenantOverride.model_validate(GOLDEN["override"]),
            approved_by=assigned.admin_user_id,
        )
    return assigned


def _rule(policy: EffectivePolicy) -> object:
    rule = policy.units.tier_limit_rule
    return rule.value if rule else None


# The keys an ``expect`` block may name, besides the per-code prefixes below.
SIMPLE: dict[str, Callable[[EffectivePolicy], object]] = {
    "pack_codes": lambda p: p.pack_codes,
    "override_version": lambda p: p.override_version,
    "tier_codes": lambda p: [tier.code for tier in p.kyc.tiers],
    "retention_kyc": lambda p: p.retention.kyc_after_relationship.value,
    "retention_transactions": lambda p: p.retention.transactions.value,
    "sanctions_alert": lambda p: p.screening.bands.sanctions_alert,
    "committee_required": lambda p: p.governance.committee_required.value,
    "geolocation_required": lambda p: p.kyc.geolocation_required.value,
    "mandatory_lists": lambda p: [e.value for e in p.screening.mandatory_lists],
    "recommended_lists": lambda p: sorted(p.screening.recommended_lists),
    "capture_codes": lambda p: sorted(c.code for c in p.captures),
    "report_codes": lambda p: sorted(r.code for r in p.reports),
    "tier_limit_rule": _rule,
}


def _actual(policy: EffectivePolicy, key: str) -> object:
    if key in SIMPLE:
        return SIMPLE[key](policy)
    if key.startswith("tier_"):
        code, _, rest = key.removeprefix("tier_").partition("_")
        tier = next(t for t in policy.kyc.tiers if t.code == code)
        value = getattr(tier.limits, rest.removesuffix("_basis"))
        return value.legal_basis if rest.endswith("_basis") else value.value
    if key.startswith("capture_"):
        code = key.removeprefix("capture_")
        return next(c for c in policy.captures if c.code == code).threshold.value
    if key.startswith("report_") and key.endswith("_deadline"):
        code = key.removeprefix("report_").removesuffix("_deadline")
        report = next(r for r in policy.reports if r.code == code)
        return report.deadline.value.model_dump(mode="json")
    msg = f"unknown golden key {key}"
    raise AssertionError(msg)


def _expected(key: str, value: Any) -> object:
    if key.startswith(("tier_N", "capture_")) and isinstance(value, dict):
        return Quantity.model_validate(value)
    if key == "sanctions_alert":
        return Decimal(value)
    if key in {"recommended_lists", "capture_codes", "report_codes"}:
        return sorted(value)
    return value


@pytest.mark.parametrize(
    "case", GOLDEN["cases"], ids=[case["id"] for case in GOLDEN["cases"]]
)
def test_effective_policy_matches_the_golden(
    overridden: CreatedTenant, case: dict[str, Any]
):
    with staff_session(overridden.tenant_id, overridden.admin_user_id) as s:
        if "error" in case:
            with pytest.raises(NotFoundError) as caught:
                _ = effective_policy(s, on=case["on"], product=case["product"])
            assert caught.value.code == case["error"]
            return
        policy = effective_policy(s, on=case["on"], product=case["product"])
    for key, value in case["expect"].items():
        assert (key, _actual(policy, key)) == (key, _expected(key, value))
