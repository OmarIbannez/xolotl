"""Tenants with policy, holidays and unit values for the monitoring tests.

Plain functions: the module's ``conftest.py`` (A4) wraps them in fixtures. Plans 4b
and 4c use these names (reconciliation finding 19).
"""

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import platform_ctx, staff_session
from tests.platform.policy_support import install_both_packs
from tests.platform.units_support import UNIT_GOLDEN, load_unit_values, seed_mx_holidays
from xolotl.modules.monitoring.enums import BatchSource
from xolotl.modules.monitoring.txn.ingest import BatchReport, ingest_batch
from xolotl.modules.monitoring.txn.schema import validate_batch
from xolotl.modules.party import service
from xolotl.modules.party.enums import CustomerRelationshipStatus, PartyType
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.party.schemas import PartyCreateRequest, PersonInput
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import tenant_session
from xolotl.platform.jobs.queue import PostgresJobQueue
from xolotl.platform.policy import assign_pack

ITF = "MX/ITF-IFPE"
AVI = "MX/LFPIORPI-AVI"
FROM = date(2026, 1, 1)
CUSTOMER = PartyCreateRequest(
    party_type=PartyType.PERSON,
    person=PersonInput(given_names="Lucía", first_surname="Ramos"),
)


def _reference_data(admin_engine: Engine) -> dict[str, UUID]:
    """Install both packs, seed Mexico's holidays and load the fixture unit values.

    Returns:
        The pack version ids by pack code.

    """
    versions = install_both_packs(admin_engine)
    seed_mx_holidays(admin_engine)
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        _ = load_unit_values(s, UNIT_GOLDEN / "values.yaml")
    return versions


def _assign(tenant: CreatedTenant, assignments: list[tuple[UUID, str | None]]) -> None:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        for version_id, product in assignments:
            _ = assign_pack(
                s,
                pack_version_id=version_id,
                product=product,
                effective_from=FROM,
                approved_by=tenant.admin_user_id,
            )


def itf_tenant(tenant: CreatedTenant, admin_engine: Engine) -> UUID:
    """Assign the ITF pack to every product of ``tenant`` from 2026-01-01.

    Mexico's holidays and ``tests/golden/units/values.yaml`` are loaded too.

    Returns:
        The tenant's id.

    """
    versions = _reference_data(admin_engine)
    _assign(tenant, [(versions[ITF], None)])
    return tenant.tenant_id


def both_packs_tenant(tenant: CreatedTenant, admin_engine: Engine) -> UUID:
    """Assign ITF to product ``EMONEY`` and AVI to ``CRYPTO`` from 2026-01-01.

    Mexico's holidays and ``tests/golden/units/values.yaml`` are loaded too.

    Returns:
        The tenant's id.

    """
    versions = _reference_data(admin_engine)
    _assign(tenant, [(versions[ITF], "EMONEY"), (versions[AVI], "CRYPTO")])
    return tenant.tenant_id


def make_item(**overrides: object) -> dict[str, object]:
    """Build a valid MXN cash deposit for ``cust-1``, ten minutes ago.

    Returns:
        The item as a client would send it, with ``overrides`` applied.

    """
    occurred = clock.now() - timedelta(minutes=10)
    item: dict[str, object] = {
        "external_id": "tx-1",
        "occurred_at": occurred.isoformat(),
        "customer_ref": "cust-1",
        "direction": "IN",
        "rail": "CASH",
        "instrument": "CASH",
        "amount": "1500.00",
        "currency": "MXN",
        "channel": "BRANCH",
    }
    item.update(overrides)
    return item


def ingest_items(
    session: Session,
    items: Sequence[Mapping[str, object]],
    *,
    queue: PostgresJobQueue,
) -> BatchReport:
    """Validate ``items`` as the route does and ingest them as an API batch.

    Returns:
        The batch report.

    Raises:
        AssertionError: When an item does not validate.

    """
    accepted, errors = validate_batch(
        items,
        now=clock.now(),
        max_future_minutes=get_settings().monitoring_max_future_minutes,
    )
    if errors:
        msg = f"items do not validate: {errors}"
        raise AssertionError(msg)
    return ingest_batch(
        session,
        accepted,
        source=BatchSource.API,
        submitted_by="api_key:test",
        queue=queue,
    )


def add_customer(
    tenant: CreatedTenant,
    external_ref: str,
    *,
    status: CustomerRelationshipStatus = CustomerRelationshipStatus.ACTIVE,
) -> CustomerRelationship:
    """Create a person and an ``EMONEY`` relationship ``external_ref`` (tier N1).

    Returns:
        The relationship, detached.

    """
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, CUSTOMER)
        row = CustomerRelationship(
            tenant_id=tenant.tenant_id,
            party_id=party.id,
            external_ref=external_ref,
            product="EMONEY",
            account_tier="N1",
            status=status,
        )
        s.add(row)
        s.flush()
        return row
