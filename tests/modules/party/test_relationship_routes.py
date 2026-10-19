"""Customer relationships through the real application.

Opening and closing resolve the tenant's effective policy; closing moves the
party's ``retention_until`` to the end of the policy's retention period.
"""

from uuid import UUID

from fastapi.testclient import TestClient

from tests.helpers import staff_session, system_ctx
from tests.modules.party.route_support import ANA_BODY, machine_headers, post
from xolotl.modules.party.enums import CustomerRelationshipStatus
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session
from xolotl.platform.ids import uuid7

PARTIES = "/v1/parties"
RELATIONSHIPS = "/v1/relationships"


def _party(client: TestClient, headers: dict[str, str]) -> str:
    created = post(client, headers, PARTIES, ANA_BODY, "p1")
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_a_machine_opens_and_closes_a_relationship_and_retention_appears_on_the_party(
    client: TestClient, tenant: CreatedTenant, itf_pack: UUID
):
    _ = itf_pack
    writer = machine_headers(tenant, "parties:write")
    reader = machine_headers(tenant, "parties:read")
    party_id = _party(client, writer)
    opened = post(
        client,
        writer,
        RELATIONSHIPS,
        {"party_id": party_id, "external_ref": "ref-1", "product": None},
        "o1",
    )
    assert opened.status_code == 201, opened.text
    assert opened.json()["status"] == "ACTIVE"
    assert opened.json()["party_id"] == party_id
    relationship_id = opened.json()["id"]
    closed = post(
        client,
        writer,
        f"{RELATIONSHIPS}/{relationship_id}/close",
        {"ended_at": "2027-03-01T12:00:00Z"},
        "c1",
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "EXITED"
    assert closed.json()["ended_at"].startswith("2027-03-01T12:00:00")
    party = client.get(f"{PARTIES}/{party_id}", headers=reader).json()
    assert party["retention_until"] == "2037-03-01"
    found = client.get(PARTIES, params={"external_ref": "ref-1"}, headers=reader)
    assert [p["id"] for p in found.json()["items"]] == [party_id]


def test_a_duplicate_external_ref_is_a_409(
    client: TestClient, tenant: CreatedTenant, itf_pack: UUID
):
    _ = itf_pack
    writer = machine_headers(tenant, "parties:write")
    party_id = _party(client, writer)
    body = {"party_id": party_id, "external_ref": "ref-1"}
    assert post(client, writer, RELATIONSHIPS, body, "o1").status_code == 201
    again = post(client, writer, RELATIONSHIPS, body, "o2")
    assert (again.status_code, again.json()["code"]) == (409, "CONFLICT")


def test_opening_without_a_pack_covering_the_product_is_a_404_policy_not_assigned(
    client: TestClient, tenant: CreatedTenant
):
    writer = machine_headers(tenant, "parties:write")
    reader = machine_headers(tenant, "parties:read")
    party_id = _party(client, writer)
    refused = post(
        client,
        writer,
        RELATIONSHIPS,
        {"party_id": party_id, "external_ref": "ref-1", "product": "EMONEY"},
        "o1",
    )
    assert (refused.status_code, refused.json()["code"]) == (
        404,
        "POLICY_NOT_ASSIGNED",
    )
    found = client.get(PARTIES, params={"external_ref": "ref-1"}, headers=reader)
    assert found.json()["items"] == []


def test_closing_without_a_pack_is_a_404_policy_not_assigned(
    client: TestClient, tenant: CreatedTenant
):
    writer = machine_headers(tenant, "parties:write")
    party_id = _party(client, writer)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        row = CustomerRelationship(
            tenant_id=tenant.tenant_id,
            party_id=UUID(party_id),
            external_ref="ref-1",
            status=CustomerRelationshipStatus.ACTIVE,
        )
        s.add(row)
        s.flush()
        relationship_id = row.id
    refused = post(
        client,
        writer,
        f"{RELATIONSHIPS}/{relationship_id}/close",
        {"ended_at": "2027-03-01T12:00:00Z"},
        "c1",
    )
    assert (refused.status_code, refused.json()["code"]) == (
        404,
        "POLICY_NOT_ASSIGNED",
    )
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        stays = s.get_one(CustomerRelationship, (tenant.tenant_id, relationship_id))
        assert stays.status is CustomerRelationshipStatus.ACTIVE
        assert stays.ended_at is None


def test_scopes_and_unknown_ids(
    client: TestClient, tenant: CreatedTenant, itf_pack: UUID
):
    _ = itf_pack
    reader = machine_headers(tenant, "parties:read")
    writer = machine_headers(tenant, "parties:write")
    party_id = _party(client, writer)
    body = {"party_id": party_id, "external_ref": "ref-1"}
    refused = post(client, reader, RELATIONSHIPS, body, "o1")
    assert (refused.status_code, refused.json()["code"]) == (403, "SCOPE_MISSING")
    unknown = post(client, writer, f"{RELATIONSHIPS}/{uuid7()}/close", {}, "c1")
    assert (unknown.status_code, unknown.json()["code"]) == (404, "NOT_FOUND")
    opened = post(client, writer, RELATIONSHIPS, body, "o2")
    assert opened.status_code == 201, opened.text
    naive = post(
        client,
        writer,
        f"{RELATIONSHIPS}/{opened.json()['id']}/close",
        {"ended_at": "2027-03-01T12:00:00"},
        "c2",
    )
    assert (naive.status_code, naive.json()["code"]) == (422, "VALIDATION_FAILED")
