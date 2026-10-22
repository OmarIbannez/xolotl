"""First sight for a tenant whose packs are assigned per product (SP-03 Decision 14).

SP-04 spec §19's tenant A has ITF on ``EMONEY`` and AVI on ``CRYPTO`` and, by D21,
no tenant-wide assignment; its new counterparties are screened all the same, under
the strictest merge of the packs in force.
"""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import Engine

from tests.helpers import staff_session
from tests.modules.screening.catalog.support import (
    ingest_fixture,
    load_shared,
    seed_sources,
)
from tests.modules.screening.counterparty.conftest import (
    OFAC,
    OFAC_XML,
    UN_ENTRY,
    Screened,
    resolve_one,
    row_of,
    runs_of,
)
from tests.platform.policy_support import install_both_packs
from xolotl.modules.screening.composition import register_screening
from xolotl.modules.screening.counterparty.first_sight import FIRST_SIGHT_KIND
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyInput,
    CounterpartyKind,
    CounterpartyScreeningStatus,
)
from xolotl.modules.screening.engine.enums import RunMode
from xolotl.modules.screening.engine.holder import set_index_holder
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.jobs import JobRunner, PostgresJobQueue
from xolotl.platform.policy import assign_pack, get_snapshot
from xolotl.platform.storage.local import LocalFsObjectStore

ALARCON = CounterpartyInput(
    kind=CounterpartyKind.PERSON, name="Rodrigo Alarcón Palomarez", country="MX"
)


@pytest.fixture
def per_product(
    tenant: CreatedTenant, admin_engine: Engine, tmp_path: Path
) -> Iterator[Screened]:
    store = LocalFsObjectStore(tmp_path / "objects")
    seed_sources(admin_engine)
    _ = ingest_fixture(OFAC, OFAC_XML, store=store)
    _ = load_shared("UN_CONSOLIDATED", [UN_ENTRY], store=store)
    versions = install_both_packs(admin_engine)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        for pack, product in (("MX/ITF-IFPE", "EMONEY"), ("MX/LFPIORPI-AVI", "CRYPTO")):
            _ = assign_pack(
                s,
                pack_version_id=versions[pack],
                product=product,
                effective_from=date(2026, 1, 1),
                approved_by=tenant.admin_user_id,
            )
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


def test_a_per_product_tenant_screens_a_new_counterparty_on_first_sight(
    tenant: CreatedTenant, per_product: Screened
):
    ref = resolve_one(tenant, ALARCON)

    assert per_product.runner.run_once() == 1

    row = row_of(tenant, ref.id)
    assert row.screening_status is CounterpartyScreeningStatus.PENDING_REVIEW
    (run,) = runs_of(tenant)
    assert run.mode is RunMode.COUNTERPARTY
    assert run.subject_id == row.screening_subject_id
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        policy = get_snapshot(s, run.policy_snapshot_id).policy
    assert policy.product is None
    assert policy.pack_codes == ["MX/ITF-IFPE"]
