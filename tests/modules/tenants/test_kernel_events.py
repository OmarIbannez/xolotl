"""Kernel events and the per-tenant platform schedules (spec §9.2, plan 1c).

The tenants service publishes ``tenant.created.v1``, ``staff.created.v1``,
``api_key.created.v1`` and ``api_key.revoked.v1`` in the transaction of the write
itself, and ``POST /v1/ping`` publishes ``test.ping.v1``. Every event is keyed by the
entity's id (the ping by the actor's) and its data holds ids and codes, never
personal data or secrets (program spec D8). Creating a tenant also schedules the
platform's jobs for it: the hourly idempotency purge and stranded-workflow sweep,
the weekday ``units.fetch`` in Mexico City, the ``lists.poll`` and
``monitoring.sweep`` every minute, the daily ``monitoring.partitions``, the
monthly ``monitoring.retention`` in UTC and the monthly
``screening.periodic_rescreen``, ``monitoring.profiles`` and
``monitoring.period_close`` in Mexico City.
"""

import json
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from tests.helpers import make_tenant, new_tenant, staff_session, system_ctx
from xolotl.modules.tenants import service
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.events.event import Event, event_from_row
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.jobs.models import JobSchedule
from xolotl.platform.jobs.queue import PostgresJobQueue, next_slot

SOURCE = "xolotl.tenants"


def events_of(tenant_id: UUID) -> list[Event]:
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(select(OutboxRow).order_by(OutboxRow.seq)).all()
        return [event_from_row(row) for row in rows]


def payloads_of(tenant_id: UUID) -> str:
    """Every stored outbox row of the tenant, as the JSON text a leak would show in.

    Returns:
        The type, key and payload of each row, as one JSON document.

    """
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(select(OutboxRow).order_by(OutboxRow.seq)).all()
        return json.dumps(
            [[row.type, row.key, row.payload] for row in rows], ensure_ascii=False
        )


def of_type(tenant_id: UUID, event_type: str) -> list[Event]:
    return [event for event in events_of(tenant_id) if event.type == event_type]


def test_creating_a_tenant_publishes_tenant_and_staff_created(engine: Engine):
    before = clock.now()
    created = new_tenant()
    after = clock.now()

    tenant_event, staff_event = events_of(created.tenant_id)
    assert tenant_event.type == "tenant.created.v1"
    assert (tenant_event.source, tenant_event.tenant_id) == (SOURCE, created.tenant_id)
    assert tenant_event.key == str(created.tenant_id)
    assert tenant_event.subject == f"tenant/{created.tenant_id}"
    assert tenant_event.data == {
        "tenant_id": str(created.tenant_id),
        "slug": "acme",
        "env": "SANDBOX",
    }
    assert tenant_event.correlation_id is None  # not in an HTTP request
    assert before <= tenant_event.time <= after

    assert staff_event.type == "staff.created.v1"
    assert staff_event.key == str(created.admin_user_id)
    assert staff_event.subject == f"staff_user/{created.admin_user_id}"
    assert staff_event.data == {
        "staff_user_id": str(created.admin_user_id),
        "roles": ["ADMIN"],
    }


def test_kernel_events_carry_no_personal_data_or_secrets(engine: Engine):
    created = new_tenant()
    stored = payloads_of(created.tenant_id)
    for secret in (
        created.admin_email,
        created.admin_password,
        "Acme SA de CV",
        "Administrator",
    ):
        assert secret not in stored


def test_creating_a_tenant_schedules_the_platforms_hourly_jobs(engine: Engine):
    before = clock.now()
    created = new_tenant()
    after = clock.now()
    with tenant_session(system_ctx(created.tenant_id)) as s:
        schedules = s.scalars(select(JobSchedule).order_by(JobSchedule.name)).all()
    assert [
        (row.name, row.cron, row.tz, row.kind, row.payload, row.enabled)
        for row in schedules
    ] == [
        ("idempotency.purge", "0 * * * *", "UTC", "idempotency.purge", {}, True),
        ("lists.poll", "* * * * *", "UTC", "lists.poll", {}, True),
        (
            "monitoring.partitions",
            "0 1 * * *",
            "UTC",
            "monitoring.partitions",
            {},
            True,
        ),
        (
            "monitoring.period_close",
            "0 4 1 * *",
            "America/Mexico_City",
            "monitoring.period_close",
            {},
            True,
        ),
        (
            "monitoring.profiles",
            "0 3 2 * *",
            "America/Mexico_City",
            "monitoring.profiles",
            {},
            True,
        ),
        (
            "monitoring.retention",
            "0 5 1 * *",
            "UTC",
            "monitoring.retention",
            {},
            True,
        ),
        ("monitoring.sweep", "* * * * *", "UTC", "monitoring.sweep", {}, True),
        (
            "party.retention_sweep",
            "0 3 * * *",
            "UTC",
            "party.retention_sweep",
            {},
            True,
        ),
        (
            "screening.periodic_rescreen",
            "0 2 1 * *",
            "America/Mexico_City",
            "screening.periodic_rescreen",
            {},
            True,
        ),
        (
            "units.fetch",
            "0 18 * * 1-5",
            "America/Mexico_City",
            "units.fetch",
            {},
            True,
        ),
        ("workflow.sweep", "0 * * * *", "UTC", "workflow.sweep", {}, True),
    ]
    # The first run is the next slot, never the current one.
    daily = {"units.fetch", "party.retention_sweep", "screening.periodic_rescreen"}
    daily |= {"monitoring.partitions", "monitoring.retention", "monitoring.profiles"}
    daily.add("monitoring.period_close")
    minutely = {"lists.poll", "monitoring.sweep"}
    hourly = [row for row in schedules if row.name not in {*daily, *minutely}]
    slots = {next_slot("0 * * * *", "UTC", after=moment) for moment in (before, after)}
    assert {row.next_run_at for row in hourly} <= slots
    by_name = {row.name: row for row in schedules}
    # Every other schedule's first run is its own next slot: a daily 03:00 UTC
    # slot is never the next full hour, the monthly re-screen runs at 02:00 on the
    # 1st in Mexico City, the lists poll and the monitoring sweep every minute,
    # partitions daily at 01:00 UTC, retention monthly at 05:00 UTC on the 1st,
    # behaviour profiles monthly at 03:00 on the 2nd and the capture period close
    # monthly at 04:00 on the 1st, both in Mexico City.
    for name, cron, tz in (
        ("units.fetch", "0 18 * * 1-5", "America/Mexico_City"),
        ("party.retention_sweep", "0 3 * * *", "UTC"),
        ("screening.periodic_rescreen", "0 2 1 * *", "America/Mexico_City"),
        ("lists.poll", "* * * * *", "UTC"),
        ("monitoring.sweep", "* * * * *", "UTC"),
        ("monitoring.partitions", "0 1 * * *", "UTC"),
        ("monitoring.retention", "0 5 1 * *", "UTC"),
        ("monitoring.profiles", "0 3 2 * *", "America/Mexico_City"),
        ("monitoring.period_close", "0 4 1 * *", "America/Mexico_City"),
    ):
        assert by_name[name].next_run_at in {
            next_slot(cron, tz, after=m) for m in (before, after)
        }, name
    assert all(row.last_run_at is None for row in schedules)


def test_a_tenant_whose_creation_fails_leaves_no_event_and_no_schedule(
    engine: Engine, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    def refuse(*_args: object, **_kwargs: object) -> UUID:
        msg = "scheduling is down"
        raise RuntimeError(msg)

    # The schedules are declared after both events are published, so a failure there
    # shows that the events belong to the tenant's own transaction.
    monkeypatch.setattr(PostgresJobQueue, "schedule", refuse)
    with pytest.raises(RuntimeError, match="scheduling is down"):
        _ = new_tenant()
    with admin_engine.connect() as conn:  # the owner sees every tenant's rows
        counts = [
            conn.execute(text("SELECT count(*) FROM tenant")).scalar_one(),
            conn.execute(text("SELECT count(*) FROM outbox")).scalar_one(),
            conn.execute(text("SELECT count(*) FROM job_schedule")).scalar_one(),
        ]
    assert counts == [0, 0, 0]


def test_creating_staff_publishes_staff_created_in_the_requests_correlation(
    client: TestClient, tenant: CreatedTenant, admin_headers: dict[str, str]
):
    created = client.post(
        "/v1/staff",
        json={"email": "ana@acme.mx", "display_name": "Ana Pérez", "roles": ["MLRO"]},
        headers={**admin_headers, "Idempotency-Key": "s1", "X-Request-Id": "req-42"},
    )
    assert created.status_code == 201, created.text
    staff_id = created.json()["id"]

    (event,) = [
        e for e in of_type(tenant.tenant_id, "staff.created.v1") if e.key == staff_id
    ]
    assert event.subject == f"staff_user/{staff_id}"
    assert event.data == {"staff_user_id": staff_id, "roles": ["MLRO"]}
    assert event.correlation_id == "req-42"
    stored = payloads_of(tenant.tenant_id)
    assert "ana@acme.mx" not in stored
    assert "Ana Pérez" not in stored
    assert created.json()["initial_password"] not in stored


class RolledBackError(Exception):
    """Raised inside the caller's transaction after the service call succeeded."""


def _create_staff(s: Session, _key_id: UUID) -> None:
    _ = service.create_staff(
        s, email="beto@acme.mx", display_name="Beto", roles=[Role.ANALYST]
    )


def _create_key(s: Session, _key_id: UUID) -> None:
    _ = service.create_api_key(s, name="ingest", scopes=["tenants:read"])


def _revoke_key(s: Session, key_id: UUID) -> None:
    _ = service.revoke_api_key(s, key_id)


@pytest.mark.parametrize(
    "call",
    [_create_staff, _create_key, _revoke_key],
    ids=["create_staff", "create_api_key", "revoke_api_key"],
)
def test_the_event_belongs_to_the_transaction_of_the_write(
    engine: Engine, tenant: CreatedTenant, call: Callable[[Session, UUID], None]
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        key_id = service.create_api_key(s, name="old", scopes=["tenants:read"]).id
    published = events_of(tenant.tenant_id)
    with (
        pytest.raises(RolledBackError),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        call(s, key_id)
        raise RolledBackError
    assert events_of(tenant.tenant_id) == published


def test_api_keys_publish_created_and_revoked_once(
    client: TestClient, tenant: CreatedTenant, admin_headers: dict[str, str]
):
    created = client.post(
        "/v1/api-keys",
        json={"name": "Ana's laptop", "scopes": ["tenants:read"]},
        headers={**admin_headers, "Idempotency-Key": "k1"},
    ).json()
    key_id = created["id"]
    for attempt in ("r1", "r2"):  # the second revoke changes nothing
        revoked = client.post(
            f"/v1/api-keys/{key_id}/revoke",
            headers={**admin_headers, "Idempotency-Key": attempt},
        )
        assert revoked.status_code == 200

    (made,) = of_type(tenant.tenant_id, "api_key.created.v1")
    assert (made.key, made.subject) == (key_id, f"api_key/{key_id}")
    assert made.data == {
        "api_key_id": key_id,
        "scopes": ["tenants:read"],
        "env": "SANDBOX",
    }
    (gone,) = of_type(tenant.tenant_id, "api_key.revoked.v1")
    assert (gone.key, gone.data) == (key_id, {"api_key_id": key_id})
    stored = payloads_of(tenant.tenant_id)
    assert created["key"] not in stored
    assert created["key_prefix"] not in stored
    assert "Ana's laptop" not in stored


def test_ping_publishes_test_ping_keyed_by_the_calling_actor(
    client: TestClient, tenant: CreatedTenant, admin_headers: dict[str, str]
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        issued = service.create_api_key(s, name="probe", scopes=["tenants:write"])
    machine = {"Authorization": f"Bearer {issued.key}"}
    callers = (
        (admin_headers, str(tenant.admin_user_id), "STAFF"),
        (machine, str(issued.id), "API_CLIENT"),
    )
    for number, (headers, actor_id, actor_type) in enumerate(callers):
        pong = client.post(
            "/v1/ping",
            json={"message": "private note"},
            headers={**headers, "Idempotency-Key": f"p{number}"},
        )
        assert pong.status_code == 200, pong.text
        assert pong.json()["actor_id"] == actor_id
        (event,) = [
            e for e in of_type(tenant.tenant_id, "test.ping.v1") if e.key == actor_id
        ]
        assert event.subject == f"actor/{actor_id}"
        assert event.data == {"actor_id": actor_id, "actor_type": actor_type}
        assert datetime.fromisoformat(pong.json()["at"]) == event.time
    assert "private note" not in payloads_of(tenant.tenant_id)


def test_every_tenant_can_be_given_the_platforms_schedules_again(engine: Engine):
    # Tenants created before a platform schedule existed get it when a worker loop
    # starts; tenants that have it keep it as it is.
    created = new_tenant()
    legacy = make_tenant("legacy")
    factory = get_sessionmaker()
    assert service.ensure_platform_schedules(factory, PostgresJobQueue(factory)) == 2
    for tenant_id in (created.tenant_id, legacy):
        with tenant_session(system_ctx(tenant_id)) as s:
            names = list(s.scalars(select(JobSchedule.name).order_by(JobSchedule.name)))
        assert names == [
            "idempotency.purge",
            "lists.poll",
            "monitoring.partitions",
            "monitoring.period_close",
            "monitoring.profiles",
            "monitoring.retention",
            "monitoring.sweep",
            "party.retention_sweep",
            "screening.periodic_rescreen",
            "units.fetch",
            "workflow.sweep",
        ]
