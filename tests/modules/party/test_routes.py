"""The party API: staff and machine views, identifier search and sub-resources.

Through the real application. Every view is masked; only ``/pii`` shows plaintext,
to staff, with one ``VIEW_PII`` row per field group.
"""

import json
import logging
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select

from tests.conftest import ADMIN_PASSWORD
from tests.helpers import (
    audit_events,
    everything_stored,
    first_login,
    leaked,
    new_tenant,
    staff_session,
    system_ctx,
)
from tests.modules.party.conftest import payloads_of
from tests.modules.party.route_support import (
    ACME_BODY,
    ANA_BODY,
    CARLOS_BODY,
    analyst_headers,
    machine_headers,
    post,
)
from xolotl.modules.party.enums import CustomerRelationshipStatus
from xolotl.modules.party.models import CustomerRelationship, Party
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import ActorType

PARTIES = "/v1/parties"


def _party_count(tenant: CreatedTenant) -> int | None:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return s.scalar(select(func.count()).select_from(Party))


def test_staff_creates_a_person_and_finds_it_by_rfc_without_decryption(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    created = post(client, admin_headers, PARTIES, ANA_BODY, "p1")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["display_name"] == "A.M.P.G."
    assert {i["id_type"]: i["masked_value"] for i in body["identifiers"]} == {
        "RFC": "****0XY0",
        "CURP": "****MN03",
    }
    assert "PEGA760510XY0" not in created.text
    found = client.get(
        PARTIES,
        params={"id_type": "RFC", "value": " pega-760510-xy0 "},
        headers=admin_headers,
    )
    assert [p["id"] for p in found.json()["items"]] == [body["id"]]
    other = client.get(
        PARTIES,
        params={"id_type": "RFC", "value": "EKU9003173C9"},
        headers=admin_headers,
    )
    assert other.json()["items"] == []
    alone = client.get(PARTIES, params={"id_type": "RFC"}, headers=admin_headers)
    assert alone.status_code == 422
    bad = client.get(
        PARTIES,
        params={"id_type": "RFC", "value": "EKU9003173C1"},
        headers=admin_headers,
    )
    assert (bad.status_code, bad.json()["code"], bad.json()["detail"]) == (
        422,
        "IDENTIFIER_INVALID",
        "RFC: CHECK_DIGIT",
    )
    assert [
        e.action for e in audit_events(tenant.tenant_id) if e.action == "VIEW_PII"
    ] == []


def test_a_duplicate_identifier_is_a_409_with_the_party_id_for_staff_only(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    first = post(client, admin_headers, PARTIES, ANA_BODY, "p1").json()["id"]
    again = post(client, admin_headers, PARTIES, ANA_BODY, "p2")
    assert (again.status_code, again.json()["code"], again.json()["detail"]) == (
        409,
        "IDENTIFIER_EXISTS",
        first,
    )
    machine = machine_headers(tenant, "parties:write")
    refused = post(client, machine, PARTIES, ANA_BODY, "m1")
    assert (refused.status_code, refused.json()["code"]) == (409, "IDENTIFIER_EXISTS")
    assert refused.json()["detail"] == "a party with this identifier already exists"
    # A failed create leaves no party row behind.
    assert _party_count(tenant) == 1


def test_the_detail_is_masked_for_everyone_and_the_reveal_audits_view_pii(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    party_id = post(client, admin_headers, PARTIES, ANA_BODY, "p1").json()["id"]
    analyst = analyst_headers(client, tenant)
    staff = client.get(f"{PARTIES}/{party_id}", headers=analyst).json()
    assert staff["person"]["pep_status"] == "NONE"
    assert staff["contacts"] == []
    assert "Ana" not in json.dumps(staff)
    machine = machine_headers(tenant, "parties:read")
    seen = client.get(f"{PARTIES}/{party_id}", headers=machine).json()
    assert "person" not in seen
    assert "contacts" not in seen
    assert seen["display_name"] == "A.M.P.G."
    forbidden = client.get(f"{PARTIES}/{party_id}/pii", headers=machine)
    assert (forbidden.status_code, forbidden.json()["code"]) == (403, "ROLE_FORBIDDEN")
    assert [e for e in audit_events(tenant.tenant_id) if e.action == "VIEW_PII"] == []
    pii = client.get(f"{PARTIES}/{party_id}/pii", headers=analyst)
    assert pii.status_code == 200, pii.text
    assert pii.json()["given_names"] == "Ana María"
    assert {i["value"] for i in pii.json()["identifiers"]} == {
        "PEGA760510XY0",
        "PEGA760510MDFRMN03",
    }
    views = [e for e in audit_events(tenant.tenant_id) if e.action == "VIEW_PII"]
    assert [(e.entity_type, e.entity_id, (e.detail or {})["group"]) for e in views] == [
        ("party", party_id, "profile"),
        ("party", party_id, "identifiers"),
    ]
    assert views[0].actor_type == ActorType.STAFF
    assert views[0].actor_id != str(tenant.admin_user_id)
    listed = client.get(
        "/v1/audit", params={"action": "VIEW_PII"}, headers=admin_headers
    ).json()["items"]
    assert len(listed) == 2
    assert client.get("/v1/audit/verify", headers=admin_headers).json()["valid"] is True


def test_scopes_gate_machines(client: TestClient, tenant: CreatedTenant):
    reader = machine_headers(tenant, "parties:read")
    writer = machine_headers(tenant, "parties:write")
    assert client.get(PARTIES, headers=reader).status_code == 200
    refused = post(client, reader, PARTIES, ACME_BODY, "m1")
    assert (refused.status_code, refused.json()["code"]) == (403, "SCOPE_MISSING")
    assert post(client, writer, PARTIES, ACME_BODY, "m2").status_code == 201
    # parties:write does not read.
    assert client.get(PARTIES, headers=writer).status_code == 403


def test_filters_and_cursor_pagination(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    ids = [
        post(client, admin_headers, PARTIES, body, key).json()["id"]
        for body, key in ((ANA_BODY, "p1"), (CARLOS_BODY, "p2"), (ACME_BODY, "p3"))
    ]
    first = client.get(PARTIES, params={"limit": 2}, headers=admin_headers).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    rest = client.get(
        PARTIES,
        params={"limit": 2, "cursor": first["next_cursor"]},
        headers=admin_headers,
    ).json()
    assert len(rest["items"]) == 1
    assert rest["next_cursor"] is None
    paged = {p["id"] for p in first["items"] + rest["items"]}
    assert paged == set(ids)
    active = client.get(PARTIES, params={"status": "ACTIVE"}, headers=admin_headers)
    assert len(active.json()["items"]) == 3
    purged = client.get(PARTIES, params={"status": "PURGED"}, headers=admin_headers)
    assert purged.json()["items"] == []
    acme = ids[2]
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        s.add(
            CustomerRelationship(
                tenant_id=tenant.tenant_id,
                party_id=UUID(acme),
                external_ref="ref-1",
                status=CustomerRelationshipStatus.ACTIVE,
            )
        )
        s.flush()
    by_ref = client.get(
        PARTIES, params={"external_ref": "ref-1"}, headers=admin_headers
    )
    assert [p["id"] for p in by_ref.json()["items"]] == [acme]
    none = client.get(PARTIES, params={"external_ref": "ref-9"}, headers=admin_headers)
    assert none.json()["items"] == []


def test_sub_resources_are_added_and_shown_masked(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    party_id = post(client, admin_headers, PARTIES, ANA_BODY, "p1").json()["id"]
    clabe = {"id_type": "CLABE", "value": "002010077777777771"}
    added = post(
        client, admin_headers, f"{PARTIES}/{party_id}/identifiers", clabe, "i1"
    )
    assert added.status_code == 201, added.text
    assert added.json()["masked_value"] == "****7771"
    again = post(
        client, admin_headers, f"{PARTIES}/{party_id}/identifiers", clabe, "i2"
    )
    assert (again.status_code, again.json()["code"]) == (409, "IDENTIFIER_EXISTS")
    contact = post(
        client,
        admin_headers,
        f"{PARTIES}/{party_id}/contacts",
        {"kind": "EMAIL", "value": "Ana@Acme.MX"},
        "c1",
    )
    assert contact.status_code == 201, contact.text
    assert contact.json()["masked_value"] == "****e.mx"
    address = post(
        client,
        admin_headers,
        f"{PARTIES}/{party_id}/addresses",
        {
            "address_type": "RESIDENTIAL",
            "lines": {"street": "Calle Falsa", "ext_number": "123"},
            "postal_code": "06600",
        },
        "a1",
    )
    assert address.status_code == 201, address.text
    assert address.json()["postal_code"] == "06600"
    assert "Calle Falsa" not in address.text
    detail = client.get(f"{PARTIES}/{party_id}", headers=admin_headers).json()
    assert len(detail["identifiers"]) == 3
    assert len(detail["contacts"]) == 1
    assert len(detail["addresses"]) == 1
    assert detail["relationships"] == []
    machine = machine_headers(tenant, "parties:read")
    seen = client.get(f"{PARTIES}/{party_id}", headers=machine).json()
    assert "contacts" not in seen
    assert len(seen["identifiers"]) == 3


def test_another_tenant_sees_nothing(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    party_id = post(client, admin_headers, PARTIES, ANA_BODY, "p1").json()["id"]
    other = new_tenant("beta", "admin@beta.mx")
    beta = first_login(
        client, "beta", other.admin_email, other.admin_password, ADMIN_PASSWORD
    )
    missing = client.get(f"{PARTIES}/{party_id}", headers=beta)
    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")
    searched = client.get(
        PARTIES, params={"id_type": "RFC", "value": "PEGA760510XY0"}, headers=beta
    )
    assert searched.json()["items"] == []
    assert client.get(PARTIES, headers=beta).json()["items"] == []


def test_every_post_needs_an_idempotency_key_and_replays(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    keyless = client.post(PARTIES, json=ANA_BODY, headers=admin_headers)
    assert (keyless.status_code, keyless.json()["code"]) == (
        400,
        "IDEMPOTENCY_KEY_REQUIRED",
    )
    first = post(client, admin_headers, PARTIES, ANA_BODY, "p1")
    replayed = post(client, admin_headers, PARTIES, ANA_BODY, "p1")
    assert first.status_code == replayed.status_code == 201
    assert replayed.headers["Idempotent-Replay"] == "true"
    assert replayed.json()["id"] == first.json()["id"]
    assert _party_count(tenant) == 1


def test_no_plaintext_is_stored_or_logged(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    admin_engine: Engine,
    caplog,
):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="sqlalchemy.engine")
    given = f"Zaira{uuid4().hex[:6].title()}"
    body = {**ANA_BODY, "person": {**ANA_BODY["person"], "given_names": given}}
    created = post(client, admin_headers, PARTIES, body, "p1")
    assert created.status_code == 201, created.text
    party_id = created.json()["id"]
    email = f"zaira.{uuid4().hex[:8]}@acme.mx"
    street = f"Calle Zaira {uuid4().hex[:8]}"
    contact = post(
        client,
        admin_headers,
        f"{PARTIES}/{party_id}/contacts",
        {"kind": "EMAIL", "value": email},
        "c1",
    )
    assert contact.status_code == 201, contact.text
    address = post(
        client,
        admin_headers,
        f"{PARTIES}/{party_id}/addresses",
        {
            "address_type": "RESIDENTIAL",
            "lines": {"street": street, "ext_number": "123"},
            "postal_code": "06600",
        },
        "a1",
    )
    assert address.status_code == 201, address.text
    _ = client.get(f"{PARTIES}/{party_id}", headers=admin_headers)
    _ = client.get(
        f"{PARTIES}/{party_id}", headers=machine_headers(tenant, "parties:read")
    )
    _ = client.get(f"{PARTIES}/{party_id}/pii", headers=admin_headers)
    assert "Row (" in caplog.text
    stored = everything_stored(admin_engine)
    payloads = payloads_of(tenant.tenant_id)
    secrets = (
        given,
        "Pérez",
        "PEGA760510XY0",
        "PEGA760510MDFRMN03",
        email,
        street,
    )
    for secret in secrets:
        assert leaked(caplog.text, secret.encode()) == []
        assert leaked(stored, secret.encode()) == []
        assert secret not in payloads


def test_patch_updates_attributes_without_an_idempotency_key(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    party_id = post(client, admin_headers, PARTIES, ANA_BODY, "p1").json()["id"]
    patched = client.patch(
        f"{PARTIES}/{party_id}",
        json={"person": {"pep_status": "DOMESTIC"}},
        headers=admin_headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["id"] == party_id
    detail = client.get(f"{PARTIES}/{party_id}", headers=admin_headers).json()
    assert detail["person"]["pep_status"] == "DOMESTIC"
    assert audit_events(tenant.tenant_id)[-1].action == "party.updated"
    mismatched = client.patch(
        f"{PARTIES}/{party_id}",
        json={"organization": {"trade_name": "x"}},
        headers=admin_headers,
    )
    assert (mismatched.status_code, mismatched.json()["code"]) == (
        422,
        "VALIDATION_FAILED",
    )
    reader = machine_headers(tenant, "parties:read")
    refused = client.patch(
        f"{PARTIES}/{party_id}",
        json={"person": {"pep_status": "NONE"}},
        headers=reader,
    )
    assert (refused.status_code, refused.json()["code"]) == (403, "SCOPE_MISSING")


def test_party_relationships_are_added_and_shown(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    ana = post(client, admin_headers, PARTIES, ANA_BODY, "p1").json()["id"]
    acme = post(client, admin_headers, PARTIES, ACME_BODY, "p2").json()["id"]
    path = f"{PARTIES}/{ana}/relationships"
    added = post(
        client,
        admin_headers,
        path,
        {"to_party_id": acme, "rel_type": "SHAREHOLDER", "ownership_pct": "51.5"},
        "r1",
    )
    assert added.status_code == 201, added.text
    body = added.json()
    assert (body["from_party_id"], body["to_party_id"]) == (ana, acme)
    assert Decimal(body["ownership_pct"]) == Decimal("51.5")
    detail = client.get(f"{PARTIES}/{acme}", headers=admin_headers).json()
    # The stored percentage carries the column's four decimal places.
    shown = [
        {**row, "ownership_pct": Decimal(row["ownership_pct"])}
        for row in detail["relationships"]
    ]
    assert shown == [{**body, "ownership_pct": Decimal("51.5")}]
    director = post(
        client,
        admin_headers,
        path,
        {"to_party_id": acme, "rel_type": "DIRECTOR", "ownership_pct": "10"},
        "r2",
    )
    assert (director.status_code, director.json()["code"]) == (
        422,
        "VALIDATION_FAILED",
    )
    itself = post(
        client, admin_headers, path, {"to_party_id": ana, "rel_type": "DIRECTOR"}, "r3"
    )
    assert (itself.status_code, itself.json()["code"]) == (422, "VALIDATION_FAILED")
    machine = machine_headers(tenant, "parties:read")
    seen = client.get(f"{PARTIES}/{acme}", headers=machine).json()
    assert "relationships" not in seen
