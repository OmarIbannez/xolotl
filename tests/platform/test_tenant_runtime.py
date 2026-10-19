"""The env and cell a connector reads for the session's tenant."""

import pytest
from sqlalchemy import Engine

from tests.helpers import make_tenant, system_ctx
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import TenantEnv
from xolotl.platform.errors import NotFoundError
from xolotl.platform.tenancy.tenants import TenantRuntime, tenant_runtime


def test_a_tenant_session_reads_its_own_env_and_cell(engine: Engine):
    sandbox = make_tenant("sbx", env=TenantEnv.SANDBOX)
    prod = make_tenant("prd", env=TenantEnv.PROD)
    with tenant_session(system_ctx(sandbox)) as s:
        assert tenant_runtime(s, sandbox) == TenantRuntime(
            env=TenantEnv.SANDBOX, cell="mx-1"
        )
    with tenant_session(system_ctx(prod)) as s:
        assert tenant_runtime(s, prod) == TenantRuntime(env=TenantEnv.PROD, cell="mx-1")


def test_another_tenants_row_is_not_found(engine: Engine):
    a, b = make_tenant("a"), make_tenant("b")
    with tenant_session(system_ctx(a)) as s, pytest.raises(NotFoundError):
        _ = tenant_runtime(s, b)
