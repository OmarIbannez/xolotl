"""The declared profile of a customer relationship (SP-04 spec §13.3, plan 4c C8).

``PATCH /v1/relationships/{id}`` sets or clears ``declared_profile``: monthly
inflow and outflow in MXN minor units (Deviation 12), the expected countries and
instruments. Each change is audited ``relationship.declared_profile_set`` with the
currency only, never an amount.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi.testclient import TestClient

from tests.helpers import (
    audit_events,
    first_login,
    new_tenant,
    staff_session,
    system_ctx,
)
from tests.modules.party.route_support import (
    ANA_BODY,
    machine_headers,
    post,
    staff_headers,
)
from xolotl.modules.party import customer, service
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.party.schemas import DeclaredProfile
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.ids import uuid7

if TYPE_CHECKING:
    from pydantic import JsonValue

PARTIES = "/v1/parties"
RELATIONSHIPS = "/v1/relationships"
ACTION = "relationship.declared_profile_set"
PROFILE = {
    "monthly_inflow_minor": 10_000_000,
    "monthly_outflow_minor": 8_000_000,
    "currency": "MXN",
    "expected_countries": ["MX", "US"],
    "expected_instruments": ["TRANSFER", "CASH"],
}


def _relationship(client: TestClient, headers: dict[str, str]) -> str:
    party = post(client, headers, PARTIES, ANA_BODY, "p1")
    assert party.status_code == 201, party.text
    opened = post(
        client,
        headers,
        RELATIONSHIPS,
        {"party_id": party.json()["id"], "external_ref": "ref-1"},
        "o1",
    )
    assert opened.status_code == 201, opened.text
    return opened.json()["id"]


def _patch(client: TestClient, headers: dict[str, str], rid: str, body: object):
    return client.patch(f"{RELATIONSHIPS}/{rid}", json=body, headers=headers)


def _stored(tenant: CreatedTenant, rid: str) -> object:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        row = s.get_one(CustomerRelationship, (tenant.tenant_id, UUID(rid)))
        return row.declared_profile


FIELDS = sorted(PROFILE)


def test_a_staff_writer_sets_and_clears_the_declared_profile_and_it_is_audited(
    client: TestClient,
    tenant: CreatedTenant,
    itf_pack: UUID,
    admin_headers: dict[str, str],
):
    _ = itf_pack
    rid = _relationship(client, admin_headers)

    resp = _patch(client, admin_headers, rid, {"declared_profile": PROFILE})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == rid
    assert resp.json()["declared_profile"] == PROFILE
    assert _stored(tenant, rid) == PROFILE
    # The same body again changes nothing and audits nothing.
    again = _patch(client, admin_headers, rid, {"declared_profile": PROFILE})
    assert again.status_code == 200, again.text
    raised = {**PROFILE, "monthly_inflow_minor": 20_000_000}
    changed = _patch(client, admin_headers, rid, {"declared_profile": raised})
    assert changed.status_code == 200, changed.text

    cleared = _patch(client, admin_headers, rid, {"declared_profile": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["declared_profile"] is None
    assert _stored(tenant, rid) is None
    assert _patch(client, admin_headers, rid, {"declared_profile": None}).is_success

    rows = [row for row in audit_events(tenant.tenant_id) if row.action == ACTION]
    assert {(r.entity_type, r.entity_id) for r in rows} == {
        ("customer_relationship", rid)
    }
    # Which fields changed, never their values: an audit row carries no amount.
    assert [r.detail for r in rows] == [
        {"currency": "MXN", "changed": FIELDS, "cleared": False},
        {"currency": "MXN", "changed": ["monthly_inflow_minor"], "cleared": False},
        {"currency": None, "changed": FIELDS, "cleared": True},
    ]


def test_a_stored_profile_outside_the_schema_is_returned_as_stored(
    client: TestClient,
    tenant: CreatedTenant,
    itf_pack: UUID,
    admin_headers: dict[str, str],
):
    # Written by something other than this PATCH (a later onboarding, a fix by
    # hand): reading the relationship must not fail on it.
    _ = itf_pack
    rid = _relationship(client, admin_headers)
    odd: dict[str, JsonValue] = {
        "currency": "USD",
        "monthly_inflow_minor": "lots",
        "source": "kyc",
    }
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        row = s.get_one(CustomerRelationship, (tenant.tenant_id, UUID(rid)))
        row.declared_profile = odd
        party_id = row.party_id
    resp = client.get(f"{PARTIES}/{party_id}", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    (listed,) = resp.json()["customer_relationships"]
    assert listed["declared_profile"] == odd


def test_relationship_refs_binds_one_array_whatever_the_number_of_ids(
    tenant: CreatedTenant, itf_pack: UUID, client: TestClient, admin_headers
):
    # One bind parameter per id would pass the protocol's 65,535 limit.
    _ = itf_pack
    rid = UUID(_relationship(client, admin_headers))
    ids = [uuid7() for _ in range(70_000)] + [rid]
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        found = service.relationship_refs(s, ids)
    assert list(found) == [rid]


def test_the_service_stores_the_json_form(
    client: TestClient,
    tenant: CreatedTenant,
    itf_pack: UUID,
    admin_headers: dict[str, str],
):
    _ = itf_pack
    rid = _relationship(client, admin_headers)
    profile = DeclaredProfile.model_validate({**PROFILE, "expected_countries": []})
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        row = customer.set_declared_profile(s, UUID(rid), profile)
        assert row.declared_profile == {**PROFILE, "expected_countries": []}


def test_a_key_with_parties_write_may_patch(
    client: TestClient, tenant: CreatedTenant, itf_pack: UUID
):
    _ = itf_pack
    writer = machine_headers(tenant, "parties:write")
    rid = _relationship(client, writer)
    resp = _patch(client, writer, rid, {"declared_profile": PROFILE})
    assert resp.status_code == 200, resp.text
    assert resp.json()["declared_profile"] == PROFILE
    reader = machine_headers(tenant, "parties:read")
    refused = _patch(client, reader, rid, {"declared_profile": PROFILE})
    assert refused.status_code == 403, refused.text


def test_a_currency_other_than_mxn_is_422(
    client: TestClient,
    tenant: CreatedTenant,
    itf_pack: UUID,
    admin_headers: dict[str, str],
):
    _ = itf_pack
    rid = _relationship(client, admin_headers)
    body = {"declared_profile": {**PROFILE, "currency": "USD"}}
    resp = _patch(client, admin_headers, rid, body)
    assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_FAILED")
    assert _stored(tenant, rid) is None


def test_an_unknown_field_is_422(
    client: TestClient,
    tenant: CreatedTenant,
    itf_pack: UUID,
    admin_headers: dict[str, str],
):
    _ = itf_pack
    rid = _relationship(client, admin_headers)
    for body in (
        {"declared_profile": {**PROFILE, "net_worth_minor": 1}},
        {"declared_profile": {**PROFILE, "monthly_inflow_minor": -1}},
        {"declared_profile": {**PROFILE, "expected_countries": ["mex"]}},
        {"declared_profile": {**PROFILE, "expected_instruments": ["cash"]}},
        {"declared_profile": PROFILE, "status": "BLOCKED"},
        {},
    ):
        resp = _patch(client, admin_headers, rid, body)
        assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_FAILED")
    assert _stored(tenant, rid) is None


def test_auditor_ro_cannot_patch(
    client: TestClient,
    tenant: CreatedTenant,
    itf_pack: UUID,
    admin_headers: dict[str, str],
):
    _ = itf_pack
    rid = _relationship(client, admin_headers)
    auditor = staff_headers(
        client, tenant, email="auditor@acme.mx", roles=(Role.AUDITOR_RO,)
    )
    resp = _patch(client, auditor, rid, {"declared_profile": PROFILE})
    assert resp.status_code == 403, resp.text
    assert _stored(tenant, rid) is None


def test_another_tenant_gets_404(
    client: TestClient,
    tenant: CreatedTenant,
    itf_pack: UUID,
    admin_headers: dict[str, str],
):
    _ = itf_pack
    rid = _relationship(client, admin_headers)
    other = new_tenant("beta", "admin@beta.mx")
    headers = first_login(
        client,
        other.slug,
        other.admin_email,
        other.admin_password,
        "another-admin-password-long",
    )
    resp = _patch(client, headers, rid, {"declared_profile": PROFILE})
    assert (resp.status_code, resp.json()["code"]) == (404, "NOT_FOUND")
    assert _stored(tenant, rid) is None
