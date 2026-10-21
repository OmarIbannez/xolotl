"""Fixtures of the monitoring tests over the plain functions of ``support``."""

from uuid import UUID

import pytest
from sqlalchemy import Engine

from tests.modules.monitoring import support
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import get_sessionmaker
from xolotl.platform.jobs.queue import PostgresJobQueue


@pytest.fixture
def itf_tenant(tenant: CreatedTenant, admin_engine: Engine) -> UUID:
    return support.itf_tenant(tenant, admin_engine)


@pytest.fixture
def both_packs_tenant(tenant: CreatedTenant, admin_engine: Engine) -> UUID:
    return support.both_packs_tenant(tenant, admin_engine)


@pytest.fixture
def queue(engine: Engine) -> PostgresJobQueue:
    return PostgresJobQueue(get_sessionmaker())


@pytest.fixture
def customer(tenant: CreatedTenant) -> CustomerRelationship:
    return support.add_customer(tenant, "cust-1")
