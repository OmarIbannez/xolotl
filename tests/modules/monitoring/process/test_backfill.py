"""The transaction backfill: the audited request and the job (A9)."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, select

from tests.helpers import staff_session, system_ctx
from tests.modules.monitoring.support import (
    add_customer,
    declare,
    ingest_items,
    make_item,
    run_processing,
)
from xolotl.app import wire_platform
from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    Measure,
    WindowKind,
)
from xolotl.modules.monitoring.aggregates.postgres import PostgresAggregateStore
from xolotl.modules.monitoring.aggregates.store import AggregateKey
from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.errors import BackfillRangeInvalidError
from xolotl.modules.monitoring.kinds import BACKFILL_KIND
from xolotl.modules.monitoring.process.backfill import (
    REQUESTED_ACTION,
    request_backfill,
)
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import KeyKind
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import PostgresJobQueue
from xolotl.platform.jobs.runner import JobRunner
from xolotl.platform.services import PlatformServices

DAY = datetime(2026, 1, 20, 12, 0, tzinfo=UTC)
# Ten days after the 20th: beyond monitoring_late_days, so the rows are held.
LATE_RECEIPT = datetime(2026, 1, 30, 12, 0, tzinfo=UTC)
COUNT_P7D = AggregateDeclaration(
    name="count_p7d",
    function=AggregateFunction.COUNT,
    measure=Measure.ONE,
    unit_key=None,
    key_kind=KeyKind.PARTY,
    window_kind=WindowKind.DURATION,
    window="P7D",
)


@pytest.fixture
def services(engine: Engine) -> PlatformServices:
    return wire_platform(get_sessionmaker(), get_settings())


@pytest.fixture
def customer_b(tenant: CreatedTenant) -> CustomerRelationship:
    return add_customer(tenant, "cust-2")


def _ingest(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
    items: Sequence[dict[str, object]],
    *,
    now: datetime = LATE_RECEIPT,
) -> list[UUID]:
    with monkeypatch.context() as patched:
        patched.setattr(clock, "now", lambda: now)
        with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
            report = ingest_items(s, items, queue=queue)
    return list(report.transaction_ids)


def _item(number: int, at: datetime, **overrides: object) -> dict[str, object]:
    return make_item(
        external_id=f"tx-{number}",
        occurred_at=at.isoformat(),
        amount=f"{number}00.00",
        **overrides,
    )


def _rows(tenant: CreatedTenant) -> dict[str, Transaction]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return {row.external_id: row for row in s.scalars(select(Transaction))}


def _request(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    *,
    from_: date,
    to: date,
    party_id: UUID | None = None,
) -> UUID:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return request_backfill(s, from_=from_, to=to, party_id=party_id, queue=queue)


def _run_backfills(services: PlatformServices) -> int:
    runner = JobRunner(
        services.queue,
        services.session_factory,
        worker_id="w-backfill",
        kinds=[BACKFILL_KIND],
    )
    return runner.run_once()


def test_a_backfill_request_is_audited_validated_and_enqueued(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    today = clock.now().date()
    with pytest.raises(BackfillRangeInvalidError):
        _ = _request(tenant, queue, from_=date(2026, 1, 21), to=date(2026, 1, 20))
    with pytest.raises(BackfillRangeInvalidError):
        _ = _request(tenant, queue, from_=today, to=today + timedelta(days=2))
    job_id = _request(
        tenant,
        queue,
        from_=date(2026, 1, 1),
        to=date(2026, 1, 31),
        party_id=customer.party_id,
    )
    payload = {
        "from": "2026-01-01",
        "to": "2026-01-31",
        "party_id": str(customer.party_id),
    }
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        job = s.scalars(select(JobRow).where(JobRow.kind == BACKFILL_KIND)).one()
        assert (job.id, job.payload, job.status) == (
            job_id,
            payload,
            JobStatus.PENDING,
        )
        assert job.key is not None
        assert job.key.startswith("backfill:")
        audit = s.scalars(
            select(AuditEventRow).where(AuditEventRow.action == REQUESTED_ACTION)
        ).one()
        assert (audit.entity_type, audit.entity_id, audit.detail) == (
            "job",
            str(job_id),
            payload,
        )
        assert audit.actor_id == str(tenant.admin_user_id)


def test_the_backfill_job_reprocesses_backfill_rows_as_late(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    customer_b: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    services: PlatformServices,
    monkeypatch: pytest.MonkeyPatch,
):
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = declare(s, COUNT_P7D, queue=queue)
    _ = _ingest(
        tenant,
        queue,
        monkeypatch,
        [
            _item(1, DAY),
            _item(2, DAY + timedelta(hours=1)),
            _item(3, DAY, customer_ref="cust-2"),
            # 2026-01-15 lies outside the range requested below.
            _item(4, datetime(2026, 1, 15, 18, tzinfo=UTC)),
        ],
    )
    assert {row.status for row in _rows(tenant).values()} == {
        TransactionStatus.BACKFILL
    }
    # Held rows are neither enqueued nor processed by the normal path.
    assert run_processing(services, tenant.tenant_id) == 0
    _ = _request(tenant, queue, from_=date(2026, 1, 20), to=date(2026, 1, 21))
    assert _run_backfills(services) == 1
    rows = _rows(tenant)
    assert {name: (row.status, row.late) for name, row in rows.items()} == {
        "tx-1": (TransactionStatus.PROCESSED, True),
        "tx-2": (TransactionStatus.PROCESSED, True),
        "tx-3": (TransactionStatus.PROCESSED, True),
        "tx-4": (TransactionStatus.BACKFILL, False),
    }
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        store = PostgresAggregateStore(s)
        read = store.read(
            AggregateKey(KeyKind.PARTY, str(customer.party_id)),
            COUNT_P7D,
            at=DAY + timedelta(hours=2),
        )
        assert read.count == 2
        (job,) = s.scalars(
            select(JobRow.status).where(JobRow.kind == BACKFILL_KIND)
        ).all()
    assert job is JobStatus.DONE


def test_a_party_filter_limits_the_backfill(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    customer_b: CustomerRelationship,
    queue: PostgresJobQueue,
    itf_tenant: UUID,
    services: PlatformServices,
    monkeypatch: pytest.MonkeyPatch,
):
    _ = _ingest(
        tenant,
        queue,
        monkeypatch,
        [_item(1, DAY), _item(2, DAY, customer_ref="cust-2")],
    )
    _ = _request(
        tenant,
        queue,
        from_=date(2026, 1, 20),
        to=date(2026, 1, 20),
        party_id=customer_b.party_id,
    )
    assert _run_backfills(services) == 1
    rows = _rows(tenant)
    assert rows["tx-1"].status is TransactionStatus.BACKFILL
    assert rows["tx-2"].status is TransactionStatus.PROCESSED
