"""Fixtures shared by the screening engine tests."""

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import Engine

from tests.helpers import staff_session
from tests.platform.policy_support import install_both_packs
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.policy import assign_pack


@pytest.fixture
def itf_pack(tenant: CreatedTenant, admin_engine: Engine) -> UUID:
    """Assign the ITF pack to every product of the tenant from 2026-01-01.

    Returns:
        The assignment's id.

    """
    version_id = install_both_packs(admin_engine)["MX/ITF-IFPE"]
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return assign_pack(
            s,
            pack_version_id=version_id,
            product=None,
            effective_from=date(2026, 1, 1),
            approved_by=tenant.admin_user_id,
        )
