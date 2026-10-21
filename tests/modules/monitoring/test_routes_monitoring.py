"""The backfill request and the processing counters (SP-04 spec §15, plan 4a A10)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from tests.helpers import audit_events, staff_session, system_ctx
from tests.modules.monitoring.support import ingest_items, make_item
from tests.modules.party.route_support import (
    analyst_headers,
    machine_headers,
    mlro_headers,
    post,
)
from xolotl.modules.monitoring.kinds import BACKFILL_KIND, PROCESS_KIND
from xolotl.modules.monitoring.process.backfill import REQUESTED_ACTION
from xolotl.modules.monitoring.process.job import process_party
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import PostgresJobQueue

BACKFILL = "/v1/monitoring/backfill"
STATUS = "/v1/monitoring/status"
RECEIVED = datetime(2026, 1, 23, 12, tzinfo=UTC)


def test_mlro_requests_a_backfill_and_it_is_audited(
    client: TestClient, tenant: CreatedTenant, customer: CustomerRelationship
):
    mlro = mlro_headers(client, tenant)
    body = {
        "from": "2026-01-01",
        "to": "2026-01-10",
        "party_id": str(customer.party_id),
    }
    response = post(client, mlro, BACKFILL, body, "bf-1")
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        job = s.scalar(select(JobRow).where(JobRow.id == UUID(job_id)))
    assert job is not None
    assert job.kind == BACKFILL_KIND
    assert job.payload == {
        "from": "2026-01-01",
        "to": "2026-01-10",
        "party_id": str(customer.party_id),
    }
    (audited,) = [
        row for row in audit_events(tenant.tenant_id) if row.action == REQUESTED_ACTION
    ]
    assert audited.entity_id == job_id
    assert audited.actor_type == "STAFF"


def test_an_analyst_cannot_request_a_backfill(
    client: TestClient, tenant: CreatedTenant
):
    body = {"from": "2026-01-01", "to": "2026-01-10"}
    analyst = analyst_headers(client, tenant)
    assert post(client, analyst, BACKFILL, body, "a").status_code == 403
    machine = machine_headers(tenant, "transactions:write", "transactions:read")
    assert post(client, machine, BACKFILL, body, "m").status_code == 403
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert not s.scalars(select(JobRow).where(JobRow.kind == BACKFILL_KIND)).all()


def test_an_invalid_range_is_422(
    client: TestClient, tenant: CreatedTenant, admin_headers: dict[str, str]
):
    backwards = {"from": "2026-01-10", "to": "2026-01-01"}
    refused = post(client, admin_headers, BACKFILL, backwards, "b")
    assert refused.status_code == 422
    assert refused.json()["code"] == "BACKFILL_RANGE_INVALID"
    tomorrow = (clock.now() + timedelta(days=2)).date().isoformat()
    future = post(
        client, admin_headers, BACKFILL, {"from": "2026-01-01", "to": tomorrow}, "f"
    )
    assert future.status_code == 422
    assert future.json()["code"] == "BACKFILL_RANGE_INVALID"


def _state(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 2026-01-22 has no UDI value: "p" parks and "w" waits behind it, received over
    # an hour ago; "h" is twelve days old at receipt and is held for a backfill.
    items = [
        make_item(external_id="p", occurred_at="2026-01-22T12:00:00Z"),
        make_item(external_id="w", occurred_at="2026-01-22T13:00:00Z"),
        make_item(external_id="h", occurred_at="2026-01-10T12:00:00Z"),
    ]
    factory = get_sessionmaker()
    queue = PostgresJobQueue(factory)
    with monkeypatch.context() as patched:
        patched.setattr(clock, "now", lambda: RECEIVED)
        with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
            _ = ingest_items(s, items, queue=queue)
    _ = process_party(
        factory,
        tenant.tenant_id,
        customer.party_id,
        queue=queue,
        job=None,
        settings=get_settings(),
        actor="cli:test",
    )


def test_admin_reads_the_status_counters(
    client: TestClient,
    tenant: CreatedTenant,
    itf_tenant: UUID,
    customer: CustomerRelationship,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    _state(tenant, customer, monkeypatch)
    # A fresh row, received now, and a processing job some worker holds.
    writer = machine_headers(tenant, "transactions:write")
    fresh = {"transactions": [make_item(external_id="now")]}
    assert post(client, writer, "/v1/transactions", fresh, "k").status_code == 202
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = s.execute(
            update(JobRow)
            .where(JobRow.kind == PROCESS_KIND)
            .values(status=JobStatus.RUNNING)
        )
    response = client.get(STATUS, headers=admin_headers)
    assert response.status_code == 200, response.text
    status = response.json()
    assert status["received_by_age"] == {
        "lt_1m": 1,
        "lt_10m": 0,
        "lt_1h": 0,
        "gte_1h": 1,
    }
    assert status["parked"] == 1
    assert status["backfill"] == 1
    assert datetime.fromisoformat(status["oldest_received_at"]) == RECEIVED
    assert status["running_process_jobs"] == 1
    assert status["partitions_ahead"]["transaction"] >= 3
    assert status["partitions_ahead"]["audit_event"] >= 3


def test_the_status_of_an_idle_tenant_is_all_zero(
    client: TestClient, tenant: CreatedTenant, admin_headers: dict[str, str]
):
    status = client.get(STATUS, headers=admin_headers).json()
    assert status["received_by_age"] == {
        "lt_1m": 0,
        "lt_10m": 0,
        "lt_1h": 0,
        "gte_1h": 0,
    }
    assert (status["parked"], status["backfill"], status["running_process_jobs"]) == (
        0,
        0,
        0,
    )
    assert status["oldest_received_at"] is None


def test_only_admin_reads_the_status(client: TestClient, tenant: CreatedTenant):
    assert client.get(STATUS, headers=mlro_headers(client, tenant)).status_code == 403
    machine = machine_headers(tenant, "transactions:read")
    assert client.get(STATUS, headers=machine).status_code == 403
