"""``GET /v1/notifications`` and ``POST /v1/notifications/{id}/read`` (plan 1c).

The signed-in staff user sees the notices addressed to them and to the roles they
hold, newest first, a cursor page at a time, and marks one read. Nothing else is
visible: other users' notices, other roles', other tenants'. Machines have no inbox.
"""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from tests.helpers import first_login, new_tenant, staff_session, system_ctx
from xolotl.modules.tenants import service
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.events.notifier import InAppNotifier

T0 = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
PASSWORD = "a-long-enough-password"


def notify(
    tenant_id: UUID,
    *,
    at: datetime,
    template: str = "t",
    user: UUID | None = None,
    role: Role | None = None,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    with monkeypatch.context() as patched:
        patched.setattr(clock, "now", lambda: at)
        with tenant_session(system_ctx(tenant_id)) as s:
            notification_id = InAppNotifier().notify(
                s,
                template=template,
                data={"n": template},
                recipient_user_id=user,
                recipient_role=role,
            )
    return str(notification_id)


def staff_headers(
    client: TestClient, tenant: CreatedTenant, email: str, roles: Iterable[Role]
) -> tuple[UUID, dict[str, str]]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        created = service.create_staff(
            s, email=email, display_name="Staff", roles=list(roles)
        )
    headers = first_login(
        client, tenant.slug, email, created.initial_password, PASSWORD
    )
    return created.id, headers


def inbox(
    client: TestClient,
    headers: dict[str, str],
    *,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict:
    params = {"limit": limit, "cursor": cursor}
    response = client.get(
        "/v1/notifications",
        headers=headers,
        params={name: value for name, value in params.items() if value is not None},
    )
    assert response.status_code == 200, response.text
    return response.json()


def read(client: TestClient, headers: dict[str, str], notification_id: str, key: str):
    return client.post(
        f"/v1/notifications/{notification_id}/read",
        headers={**headers, "Idempotency-Key": key},
    )


def test_the_inbox_lists_the_users_and_their_roles_notices_newest_first(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    mlro_id, _ = staff_headers(client, tenant, "mlro@acme.mx", [Role.MLRO])
    admin = tenant.admin_user_id
    oldest = notify(tenant.tenant_id, at=T0, user=admin, monkeypatch=monkeypatch)
    middle = notify(
        tenant.tenant_id,
        at=T0 + timedelta(minutes=1),
        role=Role.ADMIN,
        monkeypatch=monkeypatch,
    )
    for_mlro = notify(
        tenant.tenant_id,
        at=T0 + timedelta(minutes=2),
        user=mlro_id,
        monkeypatch=monkeypatch,
    )
    for_role = notify(
        tenant.tenant_id,
        at=T0 + timedelta(minutes=3),
        role=Role.MLRO,
        monkeypatch=monkeypatch,
    )
    newest = notify(
        tenant.tenant_id,
        at=T0 + timedelta(minutes=4),
        user=admin,
        template="units.fetch_failed",
        monkeypatch=monkeypatch,
    )
    other = new_tenant("other", "admin@other.mx")
    _ = notify(other.tenant_id, at=T0, role=Role.ADMIN, monkeypatch=monkeypatch)

    body = inbox(client, admin_headers)

    assert [item["id"] for item in body["items"]] == [newest, middle, oldest]
    assert body["next_cursor"] is None
    assert body["items"][0] == {
        "id": newest,
        "template": "units.fetch_failed",
        "data": {"n": "units.fetch_failed"},
        "recipient_user_id": str(admin),
        "recipient_role": None,
        "created_at": "2026-10-19T09:04:00Z",
        "read_at": None,
    }
    assert body["items"][1]["recipient_role"] == "ADMIN"
    assert {for_mlro, for_role}.isdisjoint(item["id"] for item in body["items"])


def test_the_inbox_pages_with_a_cursor(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    ids = [
        notify(
            tenant.tenant_id,
            at=T0 + timedelta(minutes=minute),
            user=tenant.admin_user_id,
            monkeypatch=monkeypatch,
        )
        for minute in range(5)
    ]
    first = inbox(client, admin_headers, limit=2)
    second = inbox(client, admin_headers, limit=2, cursor=first["next_cursor"])
    third = inbox(client, admin_headers, limit=2, cursor=second["next_cursor"])
    seen = [item["id"] for page in (first, second, third) for item in page["items"]]
    assert seen == ids[::-1]
    assert third["next_cursor"] is None


def test_reading_a_notice_sets_read_at_once(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    notice = notify(
        tenant.tenant_id, at=T0, user=tenant.admin_user_id, monkeypatch=monkeypatch
    )
    read_at = T0 + timedelta(minutes=5)
    monkeypatch.setattr(clock, "now", lambda: read_at)
    first = read(client, admin_headers, notice, "r1")
    assert first.status_code == 200, first.text
    assert first.json()["read_at"] == "2026-10-19T09:05:00Z"

    monkeypatch.setattr(clock, "now", lambda: read_at + timedelta(hours=1))
    again = read(client, admin_headers, notice, "r2")  # a new request, not a replay
    assert again.status_code == 200
    assert "idempotent-replay" not in again.headers
    assert again.json()["read_at"] == "2026-10-19T09:05:00Z"
    replay = read(client, admin_headers, notice, "r1")
    assert replay.headers["idempotent-replay"] == "true"

    (item,) = inbox(client, admin_headers)["items"]
    assert item["read_at"] == "2026-10-19T09:05:00Z"


def test_only_a_recipient_can_read_a_notice(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    mlro_id, mlro_headers = staff_headers(client, tenant, "mlro@acme.mx", [Role.MLRO])
    for_mlro = notify(tenant.tenant_id, at=T0, user=mlro_id, monkeypatch=monkeypatch)
    for_role = notify(tenant.tenant_id, at=T0, role=Role.MLRO, monkeypatch=monkeypatch)
    other = new_tenant("other", "admin@other.mx")
    foreign = notify(other.tenant_id, at=T0, role=Role.ADMIN, monkeypatch=monkeypatch)
    for notice, key in ((for_mlro, "x1"), (for_role, "x2"), (foreign, "x3")):
        denied = read(client, admin_headers, notice, key)
        assert (denied.status_code, denied.json()["code"]) == (404, "NOT_FOUND")
    unknown = read(client, admin_headers, "00000000-0000-7000-8000-000000000000", "x4")
    assert unknown.status_code == 404

    assert read(client, mlro_headers, for_role, "m1").status_code == 200
    assert sorted(
        item["read_at"] is None for item in inbox(client, mlro_headers)["items"]
    ) == [False, True]


def test_machines_have_no_inbox(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    notice = notify(tenant.tenant_id, at=T0, role=Role.ADMIN, monkeypatch=monkeypatch)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        key = service.create_api_key(s, name="ingest", scopes=["tenants:read"]).key
    machine = {"Authorization": f"Bearer {key}"}
    listed = client.get("/v1/notifications", headers=machine)
    assert (listed.status_code, listed.json()["code"]) == (403, "ROLE_FORBIDDEN")
    marked = read(client, machine, notice, "k1")
    assert (marked.status_code, marked.json()["code"]) == (403, "ROLE_FORBIDDEN")


def test_a_read_only_auditor_sees_the_inbox_but_cannot_mark_it(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    auditor_id, auditor = staff_headers(
        client, tenant, "auditor@acme.mx", [Role.AUDITOR_RO]
    )
    notice = notify(tenant.tenant_id, at=T0, user=auditor_id, monkeypatch=monkeypatch)
    assert [item["id"] for item in inbox(client, auditor)["items"]] == [notice]
    marked = read(client, auditor, notice, "a1")
    assert (marked.status_code, marked.json()["code"]) == (403, "ROLE_FORBIDDEN")
