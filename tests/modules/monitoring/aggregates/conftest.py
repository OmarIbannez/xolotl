"""A tenant session and a job queue for the Postgres aggregate tests."""

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from tests.helpers import system_ctx
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.jobs.queue import PostgresJobQueue


@pytest.fixture
def queue() -> PostgresJobQueue:
    return PostgresJobQueue(get_sessionmaker())


@pytest.fixture
def s(tenant: CreatedTenant) -> Iterator[Session]:
    """Open a system session of ``tenant``; it commits when the test ends cleanly.

    Yields:
        The session.

    """
    with tenant_session(system_ctx(tenant.tenant_id)) as session:
        yield session
