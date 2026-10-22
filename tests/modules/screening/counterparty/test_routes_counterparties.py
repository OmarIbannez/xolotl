"""The staff counterparties API (SP-04 plan 4c, C3; spec §15 and §17).

The list and the detail are masked: initials and the last four characters of an
account, a wallet or an identifier, computed by decrypting for processing (D6), so
nothing is audited. The reveal is A10's, one ``VIEW_PII`` row per value. API clients
never reach these routes (tipping-off, spec §15), and another tenant's counterparty
is not found.
"""

from uuid import uuid4

from fastapi.testclient import TestClient

from tests.helpers import audit_events, leaked, new_tenant
from tests.modules.party.route_support import machine_headers
from tests.modules.screening.counterparty.conftest import (
    VALERIAN_XBT,
    Screened,
    resolve_one,
)
from tests.modules.screening.route_support import staff_client
from xolotl.modules.party.enums import IdentifierType
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyIdentifier,
    CounterpartyInput,
    CounterpartyKind,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.audit import VIEW_PII
from xolotl.platform.enums import Role

COUNTERPARTIES = "/v1/counterparties"
NAME = "Juan Pérez Gómez"
ACCOUNT = "012180001234567897"
WALLET = "bc1qexampleexampleexampleexample0000a1b2"
RFC = "PEGJ800101AAA"
FULL = CounterpartyInput(
    kind=CounterpartyKind.PERSON,
    name=NAME,
    account=ACCOUNT,
    institution="BBVA",
    country="MX",
    wallet_address=WALLET,
    wallet_network="XBT",
    identifier=CounterpartyIdentifier(id_type=IdentifierType.RFC, value=RFC),
)
ANA = CounterpartyInput(kind=CounterpartyKind.PERSON, name="Ana Ruiz", country="MX")
ALARCON = CounterpartyInput(
    kind=CounterpartyKind.PERSON, name="Rodrigo Alarcón Palomarez", country="MX"
)
VALERIAN = CounterpartyInput(wallet_address=VALERIAN_XBT, wallet_network="XBT")


def _ids(client: TestClient, headers: dict[str, str], query: str = "") -> list[str]:
    listed = client.get(f"{COUNTERPARTIES}?{query}", headers=headers)
    assert listed.status_code == 200, listed.text
    return [row["id"] for row in listed.json()["items"]]


def test_staff_list_counterparties_masked_with_status_and_counts(
    client: TestClient, tenant: CreatedTenant, admin_headers: dict[str, str]
):
    full = resolve_one(tenant, FULL)
    ana = resolve_one(tenant, ANA)

    listed = client.get(COUNTERPARTIES, headers=admin_headers)

    assert listed.status_code == 200, listed.text
    assert not leaked(listed.text, NAME.encode(), ACCOUNT.encode(), WALLET.encode())
    assert not leaked(listed.text, RFC.encode(), b"Ana Ruiz")
    items = listed.json()["items"]
    # Newest last sighting first.
    assert [row["id"] for row in items] == [str(ana.id), str(full.id)]
    row = items[1]
    assert row["initials"] == "J.P.G."
    assert row["account_masked"] == "****7897"
    assert row["wallet_masked"] == "****a1b2"
    assert row["identifier_masked"] == "****1AAA"
    assert row["identifier_type"] == "RFC"
    assert (row["kind"], row["institution"], row["country"]) == ("PERSON", "BBVA", "MX")
    assert row["wallet_network"] == "XBT"
    assert row["screening_status"] == "UNSCREENED"
    assert row["screened_at"] is None
    assert row["subject_id"] is None
    assert row["txn_count"] == 1
    assert row["first_seen_at"]
    assert row["last_seen_at"]
    assert items[0]["initials"] == "A.R."
    assert items[0]["account_masked"] is None
    assert items[0]["wallet_masked"] is None
    # Masking is processing, not a reveal.
    assert not [e for e in audit_events(tenant.tenant_id) if e.action == VIEW_PII]


def test_the_list_filters_by_status_and_paginates(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers: dict[str, str],
    screened: Screened,
):
    alarcon = resolve_one(tenant, ALARCON)
    ana = resolve_one(tenant, ANA)
    assert screened.runner.run_once() == 1
    later = resolve_one(tenant, FULL)

    assert _ids(client, admin_headers, "status=PENDING_REVIEW") == [str(alarcon.id)]
    assert _ids(client, admin_headers, "status=CLEAR") == [str(ana.id)]
    assert _ids(client, admin_headers, "status=UNSCREENED") == [str(later.id)]
    assert _ids(client, admin_headers, "status=TRUE_MATCH") == []
    assert _ids(client, admin_headers, "kind=ORGANIZATION") == []
    assert _ids(client, admin_headers, "kind=PERSON&status=CLEAR") == [str(ana.id)]

    first = client.get(f"{COUNTERPARTIES}?limit=2", headers=admin_headers).json()
    assert [row["id"] for row in first["items"]] == [str(later.id), str(ana.id)]
    cursor = first["next_cursor"]
    second = _ids(client, admin_headers, f"limit=2&cursor={cursor}")
    assert second == [str(alarcon.id)]

    seen = client.get(COUNTERPARTIES, headers=admin_headers).json()["items"]
    after = next(row for row in seen if row["id"] == str(ana.id))["last_seen_at"]
    assert _ids(client, admin_headers, f"seen_after={after}") == [
        str(later.id),
        str(ana.id),
    ]


def test_the_detail_lists_the_findings_and_runs(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers: dict[str, str],
    screened: Screened,
):
    alarcon = resolve_one(tenant, ALARCON)
    wallet = resolve_one(tenant, VALERIAN)
    assert screened.runner.run_once() == 1

    detail = client.get(f"{COUNTERPARTIES}/{alarcon.id}", headers=admin_headers)
    by_wallet = client.get(f"{COUNTERPARTIES}/{wallet.id}", headers=admin_headers)

    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["screening_status"] == "PENDING_REVIEW"
    assert body["screened_at"] is not None
    assert body["subject_id"] is not None
    assert body["findings"]
    assert {f["subject_id"] for f in body["findings"]} == {body["subject_id"]}
    assert {f["counterparty_id"] for f in body["findings"]} == {str(alarcon.id)}
    assert {f["party_id"] for f in body["findings"]} == {None}
    assert [r["mode"] for r in body["runs"]] == ["COUNTERPARTY"]
    assert "Alarcón" not in detail.text
    # A name-less counterparty is screened as a person and as an organization.
    assert by_wallet.status_code == 200, by_wallet.text
    assert len(by_wallet.json()["runs"]) == 2
    assert not by_wallet.json()["initials"]
    assert by_wallet.json()["wallet_masked"] == "****mcfd"
    assert VALERIAN_XBT not in by_wallet.text
    missing = client.get(f"{COUNTERPARTIES}/{uuid4()}", headers=admin_headers)
    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")


def test_the_reveal_records_one_view_pii_per_field(
    client: TestClient, tenant: CreatedTenant, admin_headers: dict[str, str]
):
    full = resolve_one(tenant, FULL)
    auditor = staff_client(client, tenant, Role.AUDITOR_RO)

    revealed = client.get(f"{COUNTERPARTIES}/{full.id}/reveal", headers=auditor)

    assert revealed.status_code == 200, revealed.text
    assert revealed.json() == {
        "name": NAME,
        "account": ACCOUNT,
        "wallet_address": WALLET,
        "wallet_network": "XBT",
        "identifier_type": "RFC",
        "identifier": RFC,
    }
    views = [e for e in audit_events(tenant.tenant_id) if e.action == VIEW_PII]
    assert [(e.entity_type, e.entity_id, e.detail) for e in views] == [
        ("counterparty", str(full.id), {"field": field})
        for field in ("name", "account", "wallet", "identifier")
    ]


def test_api_keys_cannot_read_counterparties(client: TestClient, tenant: CreatedTenant):
    full = resolve_one(tenant, FULL)
    machine = machine_headers(
        tenant,
        "screening:read",
        "screening:write",
        "transactions:read",
        "transactions:write",
        "parties:read",
    )

    responses = [
        client.get(COUNTERPARTIES, headers=machine),
        client.get(f"{COUNTERPARTIES}/{full.id}", headers=machine),
        client.get(f"{COUNTERPARTIES}/{full.id}/reveal", headers=machine),
    ]

    assert [r.status_code for r in responses] == [403] * 3
    assert not [e for e in audit_events(tenant.tenant_id) if e.action == VIEW_PII]


def test_another_tenant_sees_nothing(
    client: TestClient, tenant: CreatedTenant, admin_headers: dict[str, str]
):
    full = resolve_one(tenant, FULL)
    other = new_tenant("globex", "admin@globex.mx")
    theirs = staff_client(client, other, Role.MLRO)

    responses = [
        client.get(f"{COUNTERPARTIES}/{full.id}", headers=theirs),
        client.get(f"{COUNTERPARTIES}/{full.id}/reveal", headers=theirs),
    ]

    assert [(r.status_code, r.json()["code"]) for r in responses] == [
        (404, "NOT_FOUND")
    ] * 2
    assert _ids(client, theirs) == []
    assert _ids(client, admin_headers) == [str(full.id)]
    assert not [e for e in audit_events(other.tenant_id) if e.action == VIEW_PII]
