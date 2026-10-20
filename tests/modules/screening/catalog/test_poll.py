"""The ``lists.poll`` job through the job runner (the tenant half, SP-03a D3)."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine, select

from tests.helpers import system_ctx
from tests.modules.screening.catalog.support import (
    assign_tenant_wide,
    make_version,
    seed_sources,
)
from tests.platform.jobs_support import FakeClock, enqueue, fetch_job
from tests.platform.policy_support import install_both_packs
from xolotl.modules.screening.catalog import TenantListSubscription
from xolotl.modules.screening.catalog.poll import LISTS_POLL_KIND, register_lists
from xolotl.modules.screening.catalog.subscriptions import LPB_MISSING_TEMPLATE
from xolotl.modules.screening.events import LISTS_VERSION_PUBLISHED
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus
from xolotl.platform.events import InAppNotifier
from xolotl.platform.events.event import event_from_row
from xolotl.platform.events.models import Notification, OutboxRow
from xolotl.platform.jobs.queue import PostgresJobQueue
from xolotl.platform.jobs.runner import JobRunner

NOW = datetime(2026, 10, 19, 18, tzinfo=UTC)


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock(NOW)
    monkeypatch.setattr(clock, "now", fake)
    return fake


def test_the_poll_job_runs_the_tenant_half_through_the_runner(
    tenant: CreatedTenant, admin_engine: Engine, frozen: FakeClock
):
    seed_sources(admin_engine)
    packs = install_both_packs(admin_engine)
    _ = assign_tenant_wide(tenant, packs["MX/ITF-IFPE"], date(2026, 1, 1))
    version_id = make_version(admin_engine, "UN_CONSOLIDATED", 1)
    _ = register_lists(session_factory=get_sessionmaker(), notifier=InAppNotifier())
    queue = PostgresJobQueue(get_sessionmaker(), clock_fn=frozen)
    job_id = enqueue(queue, tenant.tenant_id, kind=LISTS_POLL_KIND)

    runner = JobRunner(queue, get_sessionmaker(), worker_id="w-1")
    assert runner.run_once() == 1

    assert fetch_job(tenant.tenant_id, job_id).status is JobStatus.DONE
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        codes = set(s.scalars(select(TenantListSubscription.list_code)))
        published = s.scalars(
            select(OutboxRow).where(OutboxRow.type == LISTS_VERSION_PUBLISHED)
        ).all()
        templates = set(s.scalars(select(Notification.template)))
    assert "UN_CONSOLIDATED" in codes
    assert len(codes) == 5
    assert [event_from_row(row).data["version_id"] for row in published] == [
        str(version_id)
    ]
    assert LPB_MISSING_TEMPLATE in templates
