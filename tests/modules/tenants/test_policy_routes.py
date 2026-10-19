"""The policy routes: catalogue, assignments, effective policy and overrides."""

import re
from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select

from tests.helpers import audit_events, system_ctx
from tests.modules.tenants.policy_support import (
    api_key_headers,
    holidays_seeded,
    packs_installed,
    staff_headers,
    unit_values_loaded,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.policy import TenantPackAssignment

ITF = "MX/ITF-IFPE"
AVI = "MX/LFPIORPI-AVI"


@pytest.fixture
def packs(admin_engine: Engine) -> dict[str, UUID]:
    return packs_installed(admin_engine)


@pytest.fixture
def loaded(admin_engine: Engine, engine: Engine) -> dict[tuple[str, date], int]:
    holidays_seeded(admin_engine)
    return unit_values_loaded(engine)


def _assign(
    client: TestClient,
    headers: dict[str, str],
    version_id: UUID,
    *,
    key: str = "a1",
    product: str | None = None,
):
    return client.post(
        "/v1/policy/assignments",
        json={
            "pack_version_id": str(version_id),
            "product": product,
            "effective_from": "2026-01-01",
        },
        headers={**headers, "Idempotency-Key": key},
    )


def _assignment_count(tenant_id: UUID) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return s.scalar(select(func.count()).select_from(TenantPackAssignment)) or 0


def test_the_catalogue_lists_both_packs(
    client: TestClient, admin_headers, packs: dict[str, UUID]
):
    resp = client.get("/v1/policy/packs", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [item["code"] for item in items] == [AVI, ITF]
    assert [item["id"] for item in items] == [str(packs[AVI]), str(packs[ITF])]
    assert all(re.fullmatch(r"[0-9a-f]{64}", item["content_hash"]) for item in items)
    assert items[1]["version"] == 1
    assert items[1]["effective_from"] == "2026-01-01"


def test_assign_then_effective_converts_n1_with_the_previous_month_end_udi(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    packs: dict[str, UUID],
    loaded: dict[tuple[str, date], int],
):
    assigned = _assign(client, admin_headers, packs[ITF])
    assert assigned.status_code == 201, assigned.text
    assert assigned.json()["approved_by"] == str(tenant.admin_user_id)
    assert assigned.json()["pack"] == ITF
    assert assigned.json()["product"] is None

    params = {"on": "2026-01-20", "product": "EMONEY"}
    resp = client.get("/v1/policy/effective", params=params, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["policy"]["pack_codes"] == [ITF]
    converted = body["converted"]
    assert converted["kyc.tiers[N1].limits.monthly_ops"] == {
        "amount": "6390.00",
        "currency": "MXN",
        "unit": "UDI",
        "rate": "8.52000000",
        "unit_value_id": loaded["UDI", date(2025, 12, 31)],
        "rule": "LAST_DAY_PREV_MONTH",
        "resolved_date": "2025-12-31",
    }
    ror = converted["captures[ITF_ROR_CASH].threshold"]
    assert (ror["amount"], ror["resolved_date"]) == ("92500.00", "2026-01-19")
    va = converted["captures[ITF_VA_OPS].threshold"]
    assert (va["amount"], va["resolved_date"]) == ("64575.00", "2026-01-20")
    assert body["missing_units"] == []
    assert re.fullmatch(r"[0-9a-f]{64}", body["content_hash"])
    snapshot_id = UUID(body["snapshot_id"])

    again = client.get("/v1/policy/effective", params=params, headers=admin_headers)
    assert UUID(again.json()["snapshot_id"]) == snapshot_id

    later = client.get(
        "/v1/policy/effective",
        params={"on": "2026-01-21", "product": "EMONEY"},
        headers=admin_headers,
    ).json()
    assert later["converted"]["captures[ITF_ROR_CASH].threshold"] is None
    assert later["converted"]["captures[ITF_VA_OPS].threshold"]["amount"] == (
        "64650.00"
    )
    assert later["missing_units"] == ["USD_MXN_FIX"]

    assert "policy.pack_assigned" in [
        event.action for event in audit_events(tenant.tenant_id)
    ]


def test_effective_without_an_assignment_is_404_policy_not_assigned(
    client: TestClient, admin_headers, packs: dict[str, UUID]
):
    resp = client.get(
        "/v1/policy/effective", params={"on": "2026-01-20"}, headers=admin_headers
    )
    assert (resp.status_code, resp.json()["code"]) == (404, "POLICY_NOT_ASSIGNED")


def test_a_replayed_assignment_does_not_assign_twice(
    client: TestClient, tenant: CreatedTenant, admin_headers, packs: dict[str, UUID]
):
    first = _assign(client, admin_headers, packs[ITF])
    again = _assign(client, admin_headers, packs[ITF])
    assert first.status_code == 201, first.text
    assert again.headers["Idempotent-Replay"] == "true"
    assert again.json() == first.json()
    assert _assignment_count(tenant.tenant_id) == 1

    listed = client.get("/v1/policy/assignments", headers=admin_headers).json()
    assert [item["id"] for item in listed["items"]] == [first.json()["id"]]


def test_an_overlapping_assignment_is_409(
    client: TestClient, admin_headers, packs: dict[str, UUID]
):
    assert _assign(client, admin_headers, packs[ITF]).status_code == 201
    clash = _assign(client, admin_headers, packs[AVI], key="a2", product="CRYPTO")
    assert (clash.status_code, clash.json()["code"]) == (409, "CONFLICT")


def test_assignments_are_admin_only_and_need_a_key(
    client: TestClient, tenant: CreatedTenant, admin_headers, packs: dict[str, UUID]
):
    analyst = staff_headers(client, tenant, role=Role.ANALYST)
    refused = _assign(client, analyst, packs[ITF])
    assert (refused.status_code, refused.json()["code"]) == (403, "ROLE_FORBIDDEN")

    keyless = client.post(
        "/v1/policy/assignments",
        json={"pack_version_id": str(packs[ITF]), "effective_from": "2026-01-01"},
        headers=admin_headers,
    )
    assert (keyless.status_code, keyless.json()["code"]) == (
        400,
        "IDEMPOTENCY_KEY_REQUIRED",
    )

    machine = api_key_headers(tenant, scopes=["tenants:read"])
    for path in (
        "/v1/policy/packs",
        "/v1/policy/assignments",
        "/v1/policy/overrides",
        "/v1/policy/effective",
    ):
        resp = client.get(path, headers=machine)
        assert (resp.status_code, resp.json()["code"]) == (403, "ROLE_FORBIDDEN"), path

    # Any staff role reads.
    assert client.get("/v1/policy/packs", headers=analyst).status_code == 200


def test_overrides_tighten_only_through_the_api(
    client: TestClient, tenant: CreatedTenant, admin_headers, packs: dict[str, UUID]
):
    assert _assign(client, admin_headers, packs[ITF]).status_code == 201
    tighter = client.post(
        "/v1/policy/overrides",
        json={"tier_limits": {"N1": {"monthly_ops": {"amount": "500", "unit": "UDI"}}}},
        headers={**admin_headers, "Idempotency-Key": "o1"},
    )
    assert tighter.status_code == 201, tighter.text
    assert tighter.json()["version"] == 1
    assert tighter.json()["approved_by"] == str(tenant.admin_user_id)

    looser = client.post(
        "/v1/policy/overrides",
        json={"committee_required": False},
        headers={**admin_headers, "Idempotency-Key": "o2"},
    )
    assert (looser.status_code, looser.json()["code"]) == (409, "POLICY_LOOSENING")

    listed = client.get("/v1/policy/overrides", headers=admin_headers).json()
    assert len(listed["items"]) == 1
    override = listed["items"][0]["overrides"]
    assert override["tier_limits"]["N1"]["monthly_ops"]["amount"] == "500"
    assert "policy.override_created" in [
        event.action for event in audit_events(tenant.tenant_id)
    ]


def test_an_override_without_a_pack_in_force_is_404(
    client: TestClient, admin_headers, packs: dict[str, UUID]
):
    resp = client.post(
        "/v1/policy/overrides",
        json={"committee_required": True},
        headers={**admin_headers, "Idempotency-Key": "o1"},
    )
    assert (resp.status_code, resp.json()["code"]) == (404, "POLICY_NOT_ASSIGNED")
