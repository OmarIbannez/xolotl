"""Headers and request helpers for the party route tests (C6; C7 reuses them)."""

from fastapi.testclient import TestClient
from httpx2 import Response

from tests.helpers import first_login, staff_session
from tests.modules.party.conftest import ACME, ANA, CARLOS
from xolotl.modules.tenants import service
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.enums import Role

ANALYST_PASSWORD = "an-analyst-password-of-length"
ANA_BODY = ANA.model_dump(mode="json")
CARLOS_BODY = CARLOS.model_dump(mode="json")
ACME_BODY = ACME.model_dump(mode="json")


def staff_headers(
    client: TestClient, tenant: CreatedTenant, *, email: str, roles: tuple[Role, ...]
) -> dict[str, str]:
    """Create a staff member of the tenant and sign them in.

    Returns:
        A mapping holding the member's ``Authorization`` header.

    """
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        created = service.create_staff(
            s, email=email, display_name=email.partition("@")[0], roles=roles
        )
    return first_login(
        client, tenant.slug, email, created.initial_password, ANALYST_PASSWORD
    )


def analyst_headers(client: TestClient, tenant: CreatedTenant) -> dict[str, str]:
    """Sign in a new ``ANALYST`` of the tenant.

    Returns:
        A mapping holding the analyst's ``Authorization`` header.

    """
    return staff_headers(
        client, tenant, email="ana.analyst@acme.mx", roles=(Role.ANALYST,)
    )


def mlro_headers(client: TestClient, tenant: CreatedTenant) -> dict[str, str]:
    """Sign in a new ``MLRO`` of the tenant.

    Returns:
        A mapping holding the MLRO's ``Authorization`` header.

    """
    return staff_headers(client, tenant, email="mlro@acme.mx", roles=(Role.MLRO,))


def machine_headers(tenant: CreatedTenant, *scopes: str) -> dict[str, str]:
    """Issue an API key of the tenant with the given scopes.

    Returns:
        A mapping holding the key's ``Authorization`` header.

    """
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        issued = service.create_api_key(s, name="ingest", scopes=list(scopes))
    return {"Authorization": f"Bearer {issued.key}"}


def post(
    client: TestClient, headers: dict[str, str], path: str, body: object, key: str
) -> Response:
    """Send a JSON POST with an idempotency key.

    Returns:
        The response.

    """
    return client.post(path, json=body, headers={**headers, "Idempotency-Key": key})
