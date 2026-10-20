"""The shared lists API through the real application (SP-03 spec §12).

Staff read the tenant's subscriptions with each list's active version and freshness,
the version history, the deltas with the entries' names and an entry's JSON; an API
key with ``lists:read`` reads the overview and nothing else. Only ADMIN toggles a
subscription, a mandatory list stays on, and a change is audited.
"""

import json
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update

from tests.helpers import audit_events, platform_ctx, system_ctx
from tests.modules.party.route_support import (
    analyst_headers,
    machine_headers,
    staff_headers,
)
from tests.modules.screening.catalog.support import (
    UN_V1,
    UN_V2,
    assign_tenant_wide,
    ingest_fixture,
    seed_sources,
)
from tests.platform.policy_support import install_both_packs
from xolotl.modules.screening.catalog import (
    ListEntryRow,
    ListSource,
    TenantListSubscription,
    entry_of,
)
from xolotl.modules.screening.catalog.subscriptions import reconcile_subscriptions
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.storage.local import LocalFsObjectStore

LISTS = "/v1/lists"
UN = "UN_CONSOLIDATED"
OFAC = "OFAC_SDN"
ITF = "MX/ITF-IFPE"


@pytest.fixture
def loaded(tenant: CreatedTenant, admin_engine: Engine, tmp_path: Path) -> dict:
    """Seed the catalog, assign ITF, ingest UN v1 then v2 and subscribe the tenant.

    Returns:
        The two UN ingestion reports by seq.

    """
    seed_sources(admin_engine)
    packs = install_both_packs(admin_engine)
    _ = assign_tenant_wide(tenant, packs[ITF], date(2026, 1, 1))
    store = LocalFsObjectStore(tmp_path / "objects")
    first = ingest_fixture(UN, UN_V1, store=store)
    second = ingest_fixture(UN, UN_V2, store=store)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = reconcile_subscriptions(s, today=clock.now().date())
    return {1: first, 2: second}


def _versions(client: TestClient, headers: dict[str, str], **params) -> dict:
    response = client.get(f"{LISTS}/{UN}/versions", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _entry_row(key: str, *, current: bool = True) -> ListEntryRow:
    with tenant_session(platform_ctx()) as s:
        stmt = select(ListEntryRow).where(
            ListEntryRow.list_code == UN, ListEntryRow.source_entry_key == key
        )
        if current:
            stmt = stmt.where(ListEntryRow.to_seq.is_(None))
        return s.scalars(stmt.order_by(ListEntryRow.from_seq)).all()[0]


def test_get_lists_shows_each_subscription_with_its_version_and_freshness(
    client, admin_headers, loaded, admin_engine
):
    _ = admin_engine
    with tenant_session(platform_ctx()) as s:
        _ = s.execute(
            update(ListSource)
            .where(ListSource.code == OFAC)
            .values(consecutive_failures=2, last_error="HTTP_503")
        )
    response = client.get(LISTS, headers=admin_headers)
    assert response.status_code == 200, response.text
    items = {item["code"]: item for item in response.json()["items"]}
    assert list(items) == sorted(items)
    assert set(items) == {UN, OFAC, "EU_FSF", "MX_SAT_69B", "UK_FCDO"}
    un = items[UN]
    assert un["requirement"] == "MANDATORY"
    assert un["enabled"] is True
    assert un["list_kind"] == "SANCTION"
    assert un["active_seq"] == 2
    assert un["entry_count"] == 30
    assert un["stale"] is False
    assert un["failing"] is False
    assert un["last_success_at"] is not None
    assert un["fetched_at"] is not None
    ofac = items[OFAC]
    assert ofac["requirement"] == "RECOMMENDED"
    assert ofac["active_seq"] is None
    assert ofac["entry_count"] is None
    assert ofac["stale"] is True
    assert ofac["failing"] is True
    assert ofac["last_error"] == "HTTP_503"


def test_an_api_client_with_lists_read_sees_the_overview_and_nothing_else(
    client, tenant, loaded
):
    machine = machine_headers(tenant, "lists:read")
    assert client.get(LISTS, headers=machine).status_code == 200
    version_id = loaded[2].version_id
    entry = _entry_row("6908021")
    for path in [
        f"{LISTS}/{UN}/versions",
        f"{LISTS}/{UN}/versions/{version_id}/delta",
        f"{LISTS}/{UN}/entries/{entry.id}",
    ]:
        refused = client.get(path, headers=machine)
        assert refused.status_code == 403, path
    without = machine_headers(tenant, "parties:read")
    assert client.get(LISTS, headers=without).json()["code"] == "SCOPE_MISSING"


def test_versions_page_newest_first_with_a_cursor(client, admin_headers, loaded):
    first = _versions(client, admin_headers, limit=1)
    assert [v["seq"] for v in first["items"]] == [2]
    newest = first["items"][0]
    assert newest["id"] == str(loaded[2].version_id)
    assert newest["status"] == "ACTIVE"
    assert (newest["added"], newest["changed"], newest["removed"]) == (1, 1, 1)
    assert newest["entry_count"] == 30
    assert len(newest["file_sha256"]) == 64
    assert newest["loaded_by"] == "cli:test"
    assert first["next_cursor"] is not None
    second = _versions(client, admin_headers, limit=1, cursor=first["next_cursor"])
    assert [(v["seq"], v["status"]) for v in second["items"]] == [(1, "SUPERSEDED")]
    assert second["next_cursor"] is None


def test_versions_of_an_unknown_list_are_not_found(client, admin_headers):
    response = client.get(f"{LISTS}/NO_SUCH_LIST/versions", headers=admin_headers)
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_the_delta_shows_added_changed_and_removed_with_names(
    client, admin_headers, loaded
):
    path = f"{LISTS}/{UN}/versions/{loaded[2].version_id}/delta"
    rows = []
    cursor = None
    while True:
        params = {"limit": 1} | ({"cursor": cursor} if cursor else {})
        page = client.get(path, headers=admin_headers, params=params)
        assert page.status_code == 200, page.text
        rows.extend(page.json()["items"])
        cursor = page.json()["next_cursor"]
        if cursor is None:
            break
    assert [(r["source_entry_key"], r["change"]) for r in rows] == [
        ("6908048", "REMOVED"),
        ("6908052", "CHANGED"),
        ("9999001", "ADDED"),
    ]
    removed, changed, added = rows
    assert removed["prev_name"]
    assert removed["new_name"] is None
    assert removed["new_entry_id"] is None
    assert changed["prev_name"]
    assert changed["new_name"]
    assert changed["prev_entry_id"] != changed["new_entry_id"]
    assert added["prev_name"] is None
    assert added["new_name"] == "ZEPHYRINO QUALMSBY"
    too_many = client.get(path, headers=admin_headers, params={"limit": 201})
    assert too_many.status_code == 422
    other = client.get(
        f"{LISTS}/{OFAC}/versions/{loaded[2].version_id}/delta", headers=admin_headers
    )
    assert other.status_code == 404


def test_an_entry_is_its_list_entry_json(client, admin_headers, loaded):
    _ = loaded
    row = _entry_row("6908052", current=False)
    response = client.get(f"{LISTS}/{UN}/entries/{row.id}", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entry"] == json.loads(entry_of(row).canonical_json())
    assert (body["list_code"], body["source_entry_key"]) == (UN, "6908052")
    assert (body["from_seq"], body["to_seq"]) == (1, 2)
    elsewhere = client.get(f"{LISTS}/{OFAC}/entries/{row.id}", headers=admin_headers)
    assert elsewhere.status_code == 404
    assert elsewhere.json()["code"] == "NOT_FOUND"
    missing = client.get(f"{LISTS}/{UN}/entries/{UUID(int=1)}", headers=admin_headers)
    assert missing.status_code == 404


def _toggle(client: TestClient, headers: dict[str, str], code: str, *, enabled: bool):
    return client.patch(
        f"{LISTS}/subscriptions/{code}", headers=headers, json={"enabled": enabled}
    )


def test_admin_toggles_a_recommended_list_and_it_is_audited(
    client, admin_headers, tenant, loaded
):
    _ = loaded
    off = _toggle(client, admin_headers, OFAC, enabled=False)
    assert off.status_code == 200, off.text
    assert (off.json()["code"], off.json()["enabled"]) == (OFAC, False)
    again = _toggle(client, admin_headers, OFAC, enabled=False)
    assert again.status_code == 200
    items = {
        i["code"]: i for i in client.get(LISTS, headers=admin_headers).json()["items"]
    }
    assert items[OFAC]["enabled"] is False
    changes = [
        e
        for e in audit_events(tenant.tenant_id)
        if e.action == "lists.subscription_changed"
    ]
    assert [e.detail for e in changes] == [{"list_code": OFAC, "enabled": False}]
    assert changes[0].actor_id == str(tenant.admin_user_id)
    on = _toggle(client, admin_headers, OFAC, enabled=True)
    assert on.json()["enabled"] is True
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        row = s.scalars(
            select(TenantListSubscription).where(
                TenantListSubscription.list_code == OFAC
            )
        ).one()
        assert row.changed_by == str(tenant.admin_user_id)
    unknown = _toggle(client, admin_headers, "INTERNAL_X", enabled=False)
    assert unknown.status_code == 404


def test_a_mandatory_list_cannot_be_disabled(client, admin_headers, tenant, loaded):
    _ = loaded
    refused = _toggle(client, admin_headers, UN, enabled=False)
    assert refused.status_code == 409
    assert refused.json()["code"] == "LIST_MANDATORY"
    assert "lists.subscription_changed" not in [
        e.action for e in audit_events(tenant.tenant_id)
    ]


def test_only_admin_may_toggle(client, tenant, loaded):
    _ = loaded
    analyst = analyst_headers(client, tenant)
    refused = _toggle(client, analyst, OFAC, enabled=False)
    assert (refused.status_code, refused.json()["code"]) == (403, "ROLE_FORBIDDEN")
    auditor = staff_headers(
        client, tenant, email="auditor@acme.mx", roles=(Role.AUDITOR_RO,)
    )
    assert _toggle(client, auditor, OFAC, enabled=False).status_code == 403
    assert client.get(f"{LISTS}/{UN}/versions", headers=auditor).status_code == 200
    machine = machine_headers(tenant, "lists:read")
    assert _toggle(client, machine, OFAC, enabled=False).status_code == 403
