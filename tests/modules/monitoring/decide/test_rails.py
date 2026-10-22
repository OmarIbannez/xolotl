"""Rail settings and the rails API (SP-04 spec §12.1, plan 4c C4).

A rail without a ``rail_setting`` row answers ``XOLOTL_MONITORING_DECIDE_FAIL_MODE``;
an ADMIN sets a rail's fail mode, audited ``monitoring.rail_setting_changed``. Every
staff role reads the settings, no API key does.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.helpers import audit_events, new_tenant, staff_session, system_ctx
from tests.modules.party.route_support import (
    machine_headers,
    mlro_headers,
    staff_headers,
)
from xolotl.modules.monitoring.decide.enums import FailMode
from xolotl.modules.monitoring.decide.models import RailSetting
from xolotl.modules.monitoring.decide.rails import (
    fail_mode_for,
    rail_settings,
    set_fail_mode,
)
from xolotl.modules.monitoring.txn.schema import Rail
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.config import get_settings
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role

RAILS = "/v1/monitoring/rails"
ACTION = "monitoring.rail_setting_changed"


def _put(client: TestClient, headers: dict[str, str], rail: str, mode: str):
    return client.put(f"{RAILS}/{rail}", json={"fail_mode": mode}, headers=headers)


def _defaults(fail_mode: str = "FAIL_OPEN") -> list[dict[str, object]]:
    return [
        {
            "rail": rail.value,
            "fail_mode": fail_mode,
            "explicit": False,
            "changed_by": None,
            "updated_at": None,
        }
        for rail in Rail
    ]


def test_a_rail_without_a_row_answers_the_default_fail_mode(
    client: TestClient, tenant: CreatedTenant, admin_headers: dict[str, str]
):
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert fail_mode_for(s, Rail.SPEI) is FailMode.FAIL_OPEN
    resp = client.get(RAILS, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == _defaults()


def test_the_default_follows_the_setting(tenant: CreatedTenant, monkeypatch):
    monkeypatch.setattr(get_settings(), "monitoring_decide_fail_mode", "FAIL_CLOSED")
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert fail_mode_for(s, Rail.CASH) is FailMode.FAIL_CLOSED


def test_an_admin_sets_fail_closed_and_it_is_audited(
    client: TestClient, tenant: CreatedTenant, admin_headers: dict[str, str]
):
    resp = _put(client, admin_headers, "SPEI", "FAIL_CLOSED")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rail"] == "SPEI"
    assert body["fail_mode"] == "FAIL_CLOSED"
    assert body["explicit"] is True
    assert body["changed_by"] == str(tenant.admin_user_id)
    assert body["updated_at"] is not None
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert fail_mode_for(s, Rail.SPEI) is FailMode.FAIL_CLOSED
        assert fail_mode_for(s, Rail.CODI) is FailMode.FAIL_OPEN
    events = [e for e in audit_events(tenant.tenant_id) if e.action == ACTION]
    assert len(events) == 1
    assert events[0].entity_type == "rail_setting"
    assert events[0].detail == {"rail": "SPEI", "fail_mode": "FAIL_CLOSED"}
    listed = {
        row["rail"]: row for row in client.get(RAILS, headers=admin_headers).json()
    }
    assert listed["SPEI"] == body
    assert listed["CODI"]["explicit"] is False


def test_a_second_put_updates_the_same_row(
    client: TestClient, tenant: CreatedTenant, admin_headers: dict[str, str]
):
    assert _put(client, admin_headers, "CASH", "FAIL_CLOSED").status_code == 200
    assert _put(client, admin_headers, "CASH", "FAIL_OPEN").status_code == 200
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        rows = s.scalars(select(RailSetting)).all()
    assert [(row.rail, row.fail_mode) for row in rows] == [
        (Rail.CASH, FailMode.FAIL_OPEN)
    ]
    events = [e for e in audit_events(tenant.tenant_id) if e.action == ACTION]
    assert [e.detail for e in events] == [
        {"rail": "CASH", "fail_mode": "FAIL_CLOSED"},
        {"rail": "CASH", "fail_mode": "FAIL_OPEN"},
    ]
    assert {e.entity_id for e in events} == {str(rows[0].id)}


def test_set_fail_mode_records_the_staff_member(tenant: CreatedTenant):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        row = set_fail_mode(s, Rail.SWIFT, FailMode.FAIL_CLOSED)
        assert row.changed_by == tenant.admin_user_id
        assert row.rail is Rail.SWIFT


def test_an_unknown_rail_is_404_rail_unknown(
    client: TestClient, admin_headers: dict[str, str]
):
    resp = _put(client, admin_headers, "TELEGRAPH", "FAIL_CLOSED")
    assert resp.status_code == 404
    assert resp.json()["code"] == "RAIL_UNKNOWN"


def test_a_fail_mode_outside_the_two_is_refused(
    client: TestClient, admin_headers: dict[str, str]
):
    resp = _put(client, admin_headers, "SPEI", "SIDEWAYS")
    assert resp.status_code == 422


def test_only_admin_may_put_and_every_staff_role_may_read(
    client: TestClient, tenant: CreatedTenant
):
    mlro = mlro_headers(client, tenant)
    resp = _put(client, mlro, "SPEI", "FAIL_CLOSED")
    assert resp.status_code == 403
    assert resp.json()["code"] == "ROLE_FORBIDDEN"
    for email, role in (
        ("aud@acme.mx", Role.AUDITOR_RO),
        ("ana@acme.mx", Role.ANALYST),
    ):
        headers = staff_headers(client, tenant, email=email, roles=(role,))
        assert client.get(RAILS, headers=headers).status_code == 200, role
    assert client.get(RAILS, headers=mlro).status_code == 200


def test_api_keys_cannot_read_rails(client: TestClient, tenant: CreatedTenant):
    key = machine_headers(tenant, "transactions:write", "monitoring:read")
    for resp in (
        client.get(RAILS, headers=key),
        _put(client, key, "SPEI", "FAIL_OPEN"),
    ):
        assert resp.status_code == 403
        assert resp.json()["code"] == "ROLE_FORBIDDEN"


def test_another_tenant_sees_the_defaults_only(
    client: TestClient, tenant: CreatedTenant, admin_headers: dict[str, str]
):
    assert _put(client, admin_headers, "SPEI", "FAIL_CLOSED").status_code == 200
    other = new_tenant("beta", "admin@beta.mx")
    with tenant_session(system_ctx(other.tenant_id)) as s:
        assert s.scalars(select(RailSetting)).all() == []
        assert fail_mode_for(s, Rail.SPEI) is FailMode.FAIL_OPEN
    with staff_session(other.tenant_id, other.admin_user_id) as s:
        listed = [row.model_dump(mode="json") for row in rail_settings(s)]
    assert listed == _defaults()
