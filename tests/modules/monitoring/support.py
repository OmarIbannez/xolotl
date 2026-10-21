"""Tenants with policy, holidays and unit values for the monitoring tests.

Plain functions: the module's ``conftest.py`` (A4) wraps them in fixtures. Plans 4b
and 4c use these names (reconciliation finding 19).
"""

from datetime import date
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from tests.helpers import platform_ctx, staff_session
from tests.platform.policy_support import install_both_packs
from tests.platform.units_support import UNIT_GOLDEN, load_unit_values, seed_mx_holidays
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session
from xolotl.platform.policy import assign_pack

ITF = "MX/ITF-IFPE"
AVI = "MX/LFPIORPI-AVI"
FROM = date(2026, 1, 1)


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
