"""Assignments, tighten-only overrides, the resolver and snapshots (A7)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select

from tests.helpers import audit_events, new_tenant, staff_session
from tests.platform.policy_support import both_packs, install_both_packs
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.audit import verify_chain
from xolotl.platform.errors import DomainError, NotFoundError, XolotlError
from xolotl.platform.ids import uuid7
from xolotl.platform.policy import (
    PolicyBody,
    PolicySnapshot,
    Quantity,
    TenantOverride,
    TenantPackAssignment,
    apply_override,
    assign_pack,
    create_override,
    effective_policy,
    get_snapshot,
    merge_packs,
    snapshot_policy,
)
from xolotl.platform.sqltypes import contains_float
from xolotl.platform.units import ConversionRule

ITF = "MX/ITF-IFPE"
AVI = "MX/LFPIORPI-AVI"


def _freeze(monkeypatch: pytest.MonkeyPatch, at: datetime) -> None:
    monkeypatch.setattr(clock, "now", lambda: at)


@pytest.fixture
def february(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch, datetime(2026, 2, 1, 12, tzinfo=UTC))


@pytest.fixture
def installed(admin_engine: Engine) -> dict[str, UUID]:
    return install_both_packs(admin_engine)


@pytest.fixture
def assigned(
    engine: Engine, installed: dict[str, UUID], february: None
) -> CreatedTenant:
    tenant = new_tenant()
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        for pack, product, start in (
            (ITF, "EMONEY", date(2026, 1, 1)),
            (ITF, "CROWDFUNDING", date(2026, 1, 1)),
            (AVI, "CRYPTO", date(2026, 11, 30)),
        ):
            _ = assign_pack(
                s,
                pack_version_id=installed[pack],
                product=product,
                effective_from=start,
                approved_by=tenant.admin_user_id,
            )
    return tenant


def _override(tenant: CreatedTenant, data: dict[str, Any]) -> int:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return create_override(
            s, TenantOverride.model_validate(data), approved_by=tenant.admin_user_id
        )


def _assign(
    tenant: CreatedTenant, version_id: UUID, product: str | None, start: date
) -> UUID:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return assign_pack(
            s,
            pack_version_id=version_id,
            product=product,
            effective_from=start,
            approved_by=tenant.admin_user_id,
        )


def test_merge_packs_takes_the_strictest_of_each_value():
    packs = both_packs()
    itf, avi = packs[ITF], packs[AVI]
    merged = merge_packs([itf, avi])
    assert merged.retention.kyc_after_relationship.value == "P10Y"
    assert merged.retention.transactions.value == "P10Y"
    assert merged.kyc.geolocation_required.value is True
    assert merged.governance.committee_required.value is True
    assert [t.code for t in merged.kyc.tiers] == ["N1", "N2", "N3"]
    assert sorted(c.code for c in merged.captures) == [
        "AVI_XVI_A",
        "AVI_XVI_B",
        "ITF_FX_CASH",
        "ITF_INTL_TRANSFER",
        "ITF_ROR_CASH",
        "ITF_VA_OPS",
    ]
    assert sorted(r.code for r in merged.reports) == [
        "MX_AVISO_AVI",
        "MX_F41_ROI",
        "MX_F41_ROI24",
        "MX_F41_ROR",
        "MX_ITF_INTL_TRANSFER",
    ]
    assert merged.screening.recommended_lists == [
        "EU_FSF",
        "MX_SAT_69B",
        "OFAC_SDN",
        "UK_FCDO",
    ]
    assert [p.value for p in merged.screening.mandatory_lists] == [
        "MX_LPB",
        "UN_CONSOLIDATED",
    ]
    assert merged.screening.pep.cooling_off["FOREIGN"].value is None
    lpb = merged.screening.on_true_match["MX_LPB"]
    assert lpb.action == "SUSPEND_IMMEDIATELY"
    assert lpb.report == "MX_F41_ROI24"
    rule = merged.units.tier_limit_rule
    assert rule is not None
    assert rule.value == ConversionRule.LAST_DAY_PREV_MONTH
    assert merged.ubo.ownership_threshold_pct.value == Decimal(25)
    single = merge_packs([itf])
    for name in PolicyBody.model_fields:
        assert getattr(single, name) == getattr(itf, name), name


def test_merge_packs_refuses_disagreeing_units_and_no_packs():
    itf = both_packs()[ITF]
    tier = itf.kyc.tiers[0]
    limit = tier.limits.monthly_ops
    assert limit is not None
    in_usd = limit.model_copy(update={"value": Quantity(amount=Decimal(1), unit="USD")})
    other = itf.model_copy(
        update={
            "kyc": itf.kyc.model_copy(
                update={
                    "tiers": [
                        tier.model_copy(
                            update={
                                "limits": tier.limits.model_copy(
                                    update={"monthly_ops": in_usd}
                                )
                            }
                        )
                    ]
                }
            )
        }
    )
    with pytest.raises(DomainError, match="disagree on the unit"):
        _ = merge_packs([itf, other])
    with pytest.raises(ValueError, match="at least one pack"):
        _ = merge_packs([])


def test_apply_override_never_loosens_a_stricter_pack():
    itf = both_packs()[ITF]
    override = TenantOverride.model_validate({
        "retention": {"kyc_after_relationship": "P5Y", "transactions": "P11Y"},
        "bands": {"sanctions_alert": "0.80", "id_match_floor": "0.97"},
        "tier_limits": {"N2": {"max_balance": {"amount": "9000", "unit": "UDI"}}},
        "mandatory_lists_add": ["MX_LPB", "OFAC_SDN", "OFAC_SDN"],
        "recommended_lists_add": ["OFAC_SDN", "UN_1267"],
        "committee_required": False,
    })
    body = apply_override(itf, override, version=3, approved_on=date(2026, 3, 1))
    assert body.retention.kyc_after_relationship == itf.retention.kyc_after_relationship
    assert body.retention.transactions.value == "P11Y"
    assert body.retention.transactions.legal_basis == "tenant override v3"
    assert body.screening.bands.sanctions_alert == Decimal("0.7")
    assert body.screening.bands.id_match_floor == Decimal("0.97")
    max_balance = body.kyc.tiers[1].limits.max_balance
    assert max_balance is not None
    assert max_balance.value == Quantity(amount=Decimal(9000), unit="UDI")
    assert [p.value for p in body.screening.mandatory_lists] == [
        "MX_LPB",
        "UN_CONSOLIDATED",
        "OFAC_SDN",
    ]
    assert body.screening.recommended_lists[-1] == "UN_1267"
    assert body.screening.recommended_lists.count("OFAC_SDN") == 1
    assert body.governance.committee_required == itf.governance.committee_required


def test_merge_packs_keeps_the_longer_pep_cooling_off():
    # Spec §5.3 merges strictest: a longer cooling-off keeps a former PEP a PEP
    # for longer, so it wins over the plan's "shorter wins".
    itf = both_packs()[ITF]
    pep = itf.screening.pep
    domestic = pep.cooling_off["DOMESTIC"]
    longer = itf.model_copy(
        update={
            "screening": itf.screening.model_copy(
                update={
                    "pep": pep.model_copy(
                        update={
                            "cooling_off": {
                                **pep.cooling_off,
                                "DOMESTIC": domestic.model_copy(
                                    update={"value": "P2Y"}
                                ),
                            }
                        }
                    )
                }
            )
        }
    )
    assert domestic.value == "P1Y"
    for packs in ([itf, longer], [longer, itf]):
        merged = merge_packs(packs)
        assert merged.screening.pep.cooling_off["DOMESTIC"].value == "P2Y"
        assert merged.screening.pep.cooling_off["FOREIGN"].value is None


def test_apply_override_keeps_the_review_band_below_the_alert_band():
    itf = both_packs()[ITF]
    later = itf.model_copy(
        update={
            "screening": itf.screening.model_copy(
                update={
                    "bands": itf.screening.bands.model_copy(
                        update={"sanctions_review": Decimal("0.62")}
                    )
                }
            )
        }
    )
    override = TenantOverride.model_validate({"bands": {"sanctions_alert": "0.60"}})
    bands = apply_override(
        later, override, version=1, approved_on=date(2026, 3, 1)
    ).screening.bands
    assert bands.sanctions_alert == Decimal("0.6")
    assert bands.sanctions_review == Decimal("0.6")


@pytest.mark.parametrize(
    ("data", "code", "fragment"),
    [
        pytest.param(
            {"tier_limits": {"N1": {"monthly_ops": {"amount": "800", "unit": "UDI"}}}},
            "POLICY_LOOSENING",
            "kyc.tiers[N1].limits.monthly_ops",
            id="higher-tier-limit",
        ),
        pytest.param(
            {"capture_thresholds": {"ITF_ROR_CASH": {"amount": "6000", "unit": "USD"}}},
            "POLICY_LOOSENING",
            "captures[ITF_ROR_CASH].threshold",
            id="higher-capture-threshold",
        ),
        pytest.param(
            {"retention": {"kyc_after_relationship": "P5Y"}},
            "POLICY_LOOSENING",
            "retention.kyc_after_relationship",
            id="shorter-retention",
        ),
        pytest.param(
            {"review_frequency": {"HIGH": "P12M"}},
            "POLICY_LOOSENING",
            "kyc.review_frequency[HIGH]",
            id="longer-review-high",
        ),
        pytest.param(
            {"review_frequency": {"MEDIUM": "P13M"}},
            "POLICY_LOOSENING",
            "kyc.review_frequency[MEDIUM]",
            id="longer-review-medium",
        ),
        pytest.param(
            {"bands": {"sanctions_alert": "0.75"}},
            "POLICY_LOOSENING",
            "screening.bands.sanctions_alert",
            id="higher-sanctions-alert",
        ),
        pytest.param(
            {"bands": {"id_match_floor": "0.90"}},
            "POLICY_LOOSENING",
            "screening.bands.id_match_floor",
            id="lower-id-match-floor",
        ),
        pytest.param(
            {"committee_required": False},
            "POLICY_LOOSENING",
            "governance.committee_required",
            id="no-committee",
        ),
        pytest.param(
            {"geolocation_required": False},
            "POLICY_LOOSENING",
            "kyc.geolocation_required",
            id="no-geolocation",
        ),
        pytest.param(
            {"ubo_ownership_threshold_pct": "30"},
            "POLICY_LOOSENING",
            "ubo.ownership_threshold_pct",
            id="higher-ubo-threshold",
        ),
        pytest.param(
            {"tier_limits": {"N1": {"monthly_ops": {"amount": "500", "unit": "USD"}}}},
            "VALIDATION_FAILED",
            "kyc.tiers[N1].limits.monthly_ops",
            id="another-unit",
        ),
        pytest.param(
            {"tier_limits": {"N9": {"monthly_ops": {"amount": "1", "unit": "UDI"}}}},
            "VALIDATION_FAILED",
            "N9",
            id="unknown-tier",
        ),
        pytest.param(
            {"capture_thresholds": {"NOPE": {"amount": "1", "unit": "USD"}}},
            "VALIDATION_FAILED",
            "NOPE",
            id="unknown-capture",
        ),
        pytest.param(
            {"bands": {"sanctions_alert": "0.50"}},
            "VALIDATION_FAILED",
            "sanctions_review",
            id="alert-below-review",
        ),
    ],
)
def test_overrides_that_loosen_are_refused(
    assigned: CreatedTenant, data: dict[str, Any], code: str, fragment: str
):
    with pytest.raises(XolotlError) as caught:
        _ = _override(assigned, data)
    assert caught.value.code == code
    assert fragment in caught.value.detail


def test_equal_values_and_different_spellings_are_accepted(assigned: CreatedTenant):
    data = {"review_frequency": {"MEDIUM": "P1Y", "HIGH": "P180D"}}
    assert _override(assigned, data) == 1


def test_an_override_is_validated_against_every_product_in_force(
    assigned: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    avi_capture = {
        "capture_thresholds": {"AVI_XVI_A": {"amount": "200", "unit": "UMA"}}
    }
    # On 1 February the AVI version is not in force yet: its capture is unknown.
    with pytest.raises(XolotlError) as early:
        _ = _override(assigned, avi_capture)
    assert early.value.code == "VALIDATION_FAILED"
    assert "AVI_XVI_A" in early.value.detail
    _freeze(monkeypatch, datetime(2026, 12, 1, 12, tzinfo=UTC))
    loose = {"capture_thresholds": {"AVI_XVI_A": {"amount": "300", "unit": "UMA"}}}
    with pytest.raises(XolotlError) as caught:
        _ = _override(assigned, loose)
    assert caught.value.code == "POLICY_LOOSENING"
    assert "captures[AVI_XVI_A].threshold" in caught.value.detail
    both = {
        **avi_capture,
        "tier_limits": {"N1": {"monthly_ops": {"amount": "700", "unit": "UDI"}}},
    }
    assert _override(assigned, both) == 1


def test_override_versions_count_up_and_are_audited(assigned: CreatedTenant):
    assert _override(assigned, {"committee_required": True}) == 1
    assert _override(assigned, {"bands": {"sanctions_alert": "0.60"}}) == 2
    created = [
        event.detail
        for event in audit_events(assigned.tenant_id)
        if event.action == "policy.override_created"
    ]
    assert created == [
        {"version": 1, "effective_from": "2026-02-01"},
        {"version": 2, "effective_from": "2026-02-01"},
    ]


def test_assignments_supersede_and_refuse_overlaps(
    engine: Engine, installed: dict[str, UUID]
):
    tenant = new_tenant()
    itf, avi = installed[ITF], installed[AVI]
    first = _assign(tenant, itf, None, date(2026, 1, 1))
    second = _assign(tenant, itf, None, date(2026, 6, 1))
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        row = s.get_one(TenantPackAssignment, (tenant.tenant_id, first))
        assert row.effective_to == date(2026, 5, 31)
    assigned = [
        event.detail
        for event in audit_events(tenant.tenant_id)
        if event.action == "policy.pack_assigned"
    ]
    assert assigned[-1] is not None
    assert assigned[-1]["closed_assignment_id"] == str(first)
    for start in (date(2026, 1, 1), date(2025, 6, 1)):
        with pytest.raises(DomainError) as caught:
            _ = _assign(tenant, itf, None, start)
        assert caught.value.code == "CONFLICT"
    for version_id in (avi, itf):
        with pytest.raises(DomainError) as caught:
            _ = _assign(tenant, version_id, "CRYPTO", date(2026, 12, 1))
        assert caught.value.code == "CONFLICT"
        assert "D21" in caught.value.detail
    other = new_tenant("other", "admin@other.mx")
    _ = _assign(other, itf, "EMONEY", date(2026, 1, 1))
    _ = _assign(other, avi, "CRYPTO", date(2026, 1, 1))
    with pytest.raises(DomainError) as caught:
        _ = _assign(other, itf, None, date(2026, 1, 1))
    assert caught.value.code == "CONFLICT"
    with pytest.raises(NotFoundError):
        _ = _assign(other, uuid7(), "EMONEY", date(2027, 1, 1))
    assert second != first


def test_pack_assigned_is_audited(engine: Engine, installed: dict[str, UUID]):
    tenant = new_tenant()
    vid = installed[ITF]
    row_id = _assign(tenant, vid, None, date(2026, 1, 1))
    events = [
        e for e in audit_events(tenant.tenant_id) if e.action == "policy.pack_assigned"
    ]
    assert len(events) == 1
    assert events[0].entity_type == "tenant_pack_assignment"
    assert events[0].entity_id == str(row_id)
    assert events[0].detail == {
        "pack": ITF,
        "version": 1,
        "pack_version_id": str(vid),
        "product": None,
        "effective_from": "2026-01-01",
        "closed_assignment_id": None,
    }
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        assert verify_chain(s).valid


def test_snapshots_are_deduplicated_and_frozen(assigned: CreatedTenant):
    with staff_session(assigned.tenant_id, assigned.admin_user_id) as s:
        effective = effective_policy(s, on=date(2026, 1, 20), product="EMONEY")
        first = snapshot_policy(s, effective)
        assert snapshot_policy(s, effective) == first
        assert s.scalar(select(func.count()).select_from(PolicySnapshot)) == 1
        frozen = get_snapshot(s, first)
        assert frozen.snapshot_id == first
        assert frozen.policy == effective
        assert len(frozen.content_hash) == 64
        _ = int(frozen.content_hash, 16)
        row = s.get_one(PolicySnapshot, (assigned.tenant_id, first))
        assert contains_float(row.content) is False
        later = effective_policy(s, on=date(2026, 1, 21), product="EMONEY")
        assert snapshot_policy(s, later) != first
    other = new_tenant("other", "admin@other.mx")
    with (
        staff_session(other.tenant_id, other.admin_user_id) as s,
        pytest.raises(NotFoundError),
    ):
        _ = get_snapshot(s, first)


def test_create_override_without_a_pack_is_404(engine: Engine, february: None):
    tenant = new_tenant()
    with pytest.raises(NotFoundError) as caught:
        _ = _override(tenant, {"committee_required": True})
    assert caught.value.code == "POLICY_NOT_ASSIGNED"
