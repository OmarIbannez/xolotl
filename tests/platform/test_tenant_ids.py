"""The one tenant read the background loops share, starting with the dispatcher.

Row-level security hides every tenant table from a session without a tenant, so the
background loops list the tenants through a platform-operator read of ``tenant`` and
then work inside each tenant's own scope. The outbox dispatcher goes through
``tenant_ids``; the job runner and the scheduler join it in a later change.
"""

from sqlalchemy import Engine

from tests.helpers import make_tenant
from xolotl.platform.db import get_sessionmaker
from xolotl.platform.enums import TenantStatus
from xolotl.platform.tenancy.tenants import tenant_ids


def test_every_tenant_is_listed_oldest_first_whatever_its_status(engine: Engine):
    alpha = make_tenant("alpha")
    beta = make_tenant("beta", status=TenantStatus.SUSPENDED)
    gamma = make_tenant("gamma", status=TenantStatus.OFFBOARDED)
    assert tenant_ids(get_sessionmaker(), actor_id="test-loop") == [alpha, beta, gamma]


def test_no_tenant_means_an_empty_list(engine: Engine):
    assert tenant_ids(get_sessionmaker(), actor_id="test-loop") == []
