"""Fixtures of the decide tests (plan 4c C4; C5 appends its own)."""

from uuid import UUID

import pytest
from sqlalchemy import Engine

from xolotl.modules.tenants.service import CreatedTenant


@pytest.fixture
def tenant_id(engine: Engine, tenant: CreatedTenant) -> UUID:
    return tenant.tenant_id
