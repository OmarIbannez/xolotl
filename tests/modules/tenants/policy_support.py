"""Reference data the policy, units and regulatory-profile route tests share."""

from datetime import date
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from tests.helpers import first_login, platform_ctx, staff_session
from tests.platform.policy_support import install_both_packs
from tests.platform.units_support import UNIT_GOLDEN, load_unit_values, seed_mx_holidays
from xolotl.modules.tenants import service
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role


def packs_installed(admin_engine: Engine) -> dict[str, UUID]:
    """Install ``MX/ITF-IFPE`` then ``MX/LFPIORPI-AVI`` in one owner transaction.

    Returns:
        The version ids by pack code.

    """
    return install_both_packs(admin_engine)


def holidays_seeded(admin_engine: Engine) -> None:
    seed_mx_holidays(admin_engine)


def unit_values_loaded(engine: Engine) -> dict[tuple[str, date], int]:
    """Load the fixture unit values of ``tests/golden/units/values.yaml``.

    Returns:
        ``{(unit_code, valid_from): id}`` of the current rows.

    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=factory) as s:
        return load_unit_values(s, UNIT_GOLDEN / "values.yaml")


def staff_headers(
    client: TestClient, tenant: CreatedTenant, *, role: Role
) -> dict[str, str]:
    """Create a staff user with one role and sign them in.

    Returns:
        Their ``Authorization`` header.

    """
    email = f"{role.value.lower()}@acme.mx"
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        created = service.create_staff(s, email=email, display_name=email, roles=[role])
    return first_login(
        client, tenant.slug, email, created.initial_password, "a-long-enough-password"
    )


def api_key_headers(tenant: CreatedTenant, *, scopes: list[str]) -> dict[str, str]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        key = service.create_api_key(s, name="machine", scopes=scopes).key
    return {"Authorization": f"Bearer {key}"}
