"""Clients and rule helpers of the monitoring route tests (plan 4b B9; B11, B12).

``staff_client`` signs in a new staff member with the given roles and
``api_client`` issues an API key with the given scopes; both return the headers to
send. ``activate_rule`` takes a library template all the way to ``ACTIVE`` through
the API: an author creates and simulates it (the simulation job runs here), submits
it, and an approver approves and activates it.
"""

from datetime import date, timedelta
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response

from tests.modules.party.route_support import machine_headers, staff_headers
from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.simulation.job import SIMULATE_KIND
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.db import get_sessionmaker
from xolotl.platform.enums import Role
from xolotl.platform.jobs.runner import JobRunner

if TYPE_CHECKING:
    from xolotl.platform.api.app import AppState

RULES = "/v1/monitoring/rules"


def staff_client(
    client: TestClient, tenant: CreatedTenant, *roles: Role
) -> dict[str, str]:
    """Create a staff member of the tenant with ``roles`` and sign them in.

    Returns:
        The member's ``Authorization`` header.

    """
    email = f"{roles[0].value.lower()}-{uuid4().hex[:8]}@acme.mx"
    return staff_headers(client, tenant, email=email, roles=roles)


def api_client(tenant: CreatedTenant, *scopes: str) -> dict[str, str]:
    """Issue an API key of the tenant with ``scopes``.

    Returns:
        The key's ``Authorization`` header.

    """
    return machine_headers(tenant, *scopes)


def post(
    client: TestClient,
    headers: dict[str, str],
    path: str,
    body: object = None,
    *,
    key: str | None = None,
) -> Response:
    """Send a JSON POST with an idempotency key, a new one unless ``key`` is given.

    Returns:
        The response.

    """
    return client.post(
        path,
        json=body if body is not None else {},
        headers={**headers, "Idempotency-Key": key or uuid4().hex},
    )


def today() -> date:
    """Today in Mexico City, as the lifecycle reads it.

    Returns:
        The policy date of the clock.

    """
    return policy_date(clock.now())


def run_simulations(app: FastAPI) -> int:
    """Run the queued simulation jobs of the application's queue.

    Returns:
        How many jobs ran.

    """
    services = cast("AppState", app.state.xolotl).services
    assert services is not None
    runner = JobRunner(
        services.queue, get_sessionmaker(), worker_id="w-routes", kinds=[SIMULATE_KIND]
    )
    ran = 0
    while step := runner.run_once():
        ran += step
    return ran


def simulated(
    client: TestClient,
    app: FastAPI,
    author: dict[str, str],
    rule_id: str,
    number: int,
) -> str:
    """Simulate a version over the last week and run the job.

    Returns:
        The simulation's id.

    """
    end = today()
    response = post(
        client,
        author,
        f"{RULES}/{rule_id}/versions/{number}/simulate",
        {
            "range_start": (end - timedelta(days=6)).isoformat(),
            "range_end": end.isoformat(),
        },
    )
    assert response.status_code == 202, response.text
    assert run_simulations(app) == 1
    return str(response.json()["simulation_id"])


def activate_rule(
    client: TestClient,
    app: FastAPI,
    *,
    author: dict[str, str],
    approver: dict[str, str],
    code: str,
    effective_from: date | None = None,
    product: str | None = None,
) -> dict[str, object]:
    """Take a new version of the template ``code`` to ``ACTIVE`` through the API.

    Returns:
        The active version's view.

    """
    created = post(client, author, RULES, {"template_code": code})
    assert created.status_code == 201, created.text
    version = created.json()
    rule_id, number = version["rule_id"], version["version"]
    path = f"{RULES}/{rule_id}/versions/{number}"
    _ = simulated(client, app, author, rule_id, number)
    for headers, action in ((author, "submit"), (approver, "approve")):
        response = post(client, headers, f"{path}/{action}")
        assert response.status_code == 200, (action, response.text)
    body = {
        "effective_from": (effective_from or today()).isoformat(),
        "product": product,
    }
    activated = post(client, approver, f"{path}/activate", body)
    assert activated.status_code == 200, activated.text
    return cast("dict[str, object]", activated.json())
