"""The monitoring test tenants carry the assignments the plans rely on."""

from sqlalchemy import Engine, select

from tests.helpers import staff_session
from tests.modules.monitoring.support import both_packs_tenant, itf_tenant
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.policy.models import TenantPackAssignment


def _products(tenant: CreatedTenant) -> list[str | None]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return sorted(
            s.scalars(select(TenantPackAssignment.product)).all(),
            key=lambda product: product or "",
        )


def test_itf_tenant_assigns_one_pack_to_every_product(
    tenant: CreatedTenant, admin_engine: Engine
):
    assert itf_tenant(tenant, admin_engine) == tenant.tenant_id
    assert _products(tenant) == [None]


def test_both_packs_tenant_assigns_itf_to_emoney_and_avi_to_crypto(
    tenant: CreatedTenant, admin_engine: Engine
):
    assert both_packs_tenant(tenant, admin_engine) == tenant.tenant_id
    assert _products(tenant) == ["CRYPTO", "EMONEY"]
