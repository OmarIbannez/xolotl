"""Fixtures of the counterparty screening tests (SP-04 plan 4c, C1).

``screened`` gives a tenant with the ITF pack, OFAC's trimmed SDN fixture (which
lists two digital-currency addresses) and a small UN set, with the screening module
composed on a real queue and runner, so a resolution enqueues its first-sight job and
the runner screens it.
"""

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tests.helpers import staff_session, system_ctx
from tests.modules.screening.catalog.support import (
    ingest_fixture,
    load_shared,
    seed_sources,
)
from tests.modules.screening.review.support import assign_itf
from xolotl.modules.screening.composition import register_screening
from xolotl.modules.screening.counterparty.first_sight import FIRST_SIGHT_KIND
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.resolve import CounterpartyRef, resolve
from xolotl.modules.screening.counterparty.schema import CounterpartyInput
from xolotl.modules.screening.engine.holder import set_index_holder
from xolotl.modules.screening.engine.models import ScreeningRun
from xolotl.modules.screening.lists import EntityType, EntryName, ListEntry, NameQuality
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.jobs import JobRunner, PostgresJobQueue
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.storage.local import LocalFsObjectStore

OFAC = "OFAC_SDN"
OFAC_XML = Path(__file__).parents[1] / "fixtures" / "ofac" / "sdn_enhanced.xml"
# OFAC uid 45314, VALERIAN LABS, INC. (an organization), and uid 57118, ALARCON
# PALOMARES, Rodrigo (a person), as the fixture lists them.
VALERIAN_XBT = "bc1qardcxz845jw83vgfvcmewhuxa0ag9gchjwmcfd"
ALARCON_ETH = "0xaC4cC4B68ea24BbFAAC8fD127B67Ed445ACcCE22"
UN_ENTRY = ListEntry(
    list_code="UN_CONSOLIDATED",
    entry_key="1001",
    entity_type=EntityType.PERSON,
    primary_name=EntryName("Abu Hamza Tlalocan", NameQuality.PRIMARY),
    source_ref="un 1001",
)


@dataclass(frozen=True, slots=True)
class Screened:
    queue: PostgresJobQueue
    runner: JobRunner


@pytest.fixture
def screened(
    tenant: CreatedTenant, admin_engine: Engine, tmp_path: Path
) -> Iterator[Screened]:
    store = LocalFsObjectStore(tmp_path / "objects")
    seed_sources(admin_engine)
    _ = ingest_fixture(OFAC, OFAC_XML, store=store)
    _ = load_shared("UN_CONSOLIDATED", [UN_ENTRY], store=store)
    assign_itf(tenant, admin_engine)
    factory = get_sessionmaker()
    queue = PostgresJobQueue(factory)
    reset_subscribers()
    _ = register_screening(
        session_factory=factory, settings=get_settings(), queue=queue
    )
    yield Screened(
        queue, JobRunner(queue, factory, worker_id="w-test", kinds=[FIRST_SIGHT_KIND])
    )
    reset_subscribers()
    set_index_holder(None)


@contextmanager
def job_session(tenant: CreatedTenant) -> Generator[Session]:
    """Open a session as a job does: system activity of the tenant.

    Yields:
        The session, committed on a clean exit.

    """
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        yield s


def resolve_one(
    tenant: CreatedTenant, counterparty: CounterpartyInput
) -> CounterpartyRef:
    """Resolve one counterparty in a committed session of its own, as ingest would.

    Returns:
        The reference ``resolve`` returned.

    """
    with job_session(tenant) as s:
        return resolve(s, counterparty)


def row_of(tenant: CreatedTenant, counterparty_id: UUID) -> Counterparty:
    with job_session(tenant) as s:
        return s.get_one(Counterparty, (tenant.tenant_id, counterparty_id))


def first_sight_jobs(tenant: CreatedTenant) -> list[JobRow]:
    with job_session(tenant) as s:
        return list(
            s.scalars(
                select(JobRow)
                .where(JobRow.kind == FIRST_SIGHT_KIND)
                .order_by(JobRow.created_at, JobRow.id)
            )
        )


def runs_of(tenant: CreatedTenant) -> list[ScreeningRun]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return list(
            s.scalars(
                select(ScreeningRun).order_by(ScreeningRun.created_at, ScreeningRun.id)
            )
        )
