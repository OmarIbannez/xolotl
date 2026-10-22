"""``GET /v1/monitoring/decisions/{id}``: staff see everything, a client the answer.

An API client with ``transactions:read`` reads the decision, its mode and when;
never a rule, an aggregate, a severity or a screening status (D20, PRV-502).
Staff read the rules that fired with their aggregates and the linked transaction.
"""

from collections.abc import Iterator
from uuid import UUID

from fastapi.testclient import TestClient

from tests.helpers import new_tenant, system_ctx
from tests.modules.monitoring.decide.support import (
    DECISIONS,
    UDI_100,
    UDI_350,
    DecideStage,
    ask,
    candidate,
    month_to_date,
)
from tests.modules.monitoring.rules.hook_support import TIER_N1
from tests.modules.party.route_support import machine_headers, staff_headers
from xolotl.modules.monitoring.decide.views import decision_client_view
from xolotl.modules.monitoring.schemas import DecisionClientResponse
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role

FORBIDDEN = ("rule", "aggregate", "severity", "screening", "reason", "hit")


def _keys(schema: object) -> Iterator[str]:
    if isinstance(schema, dict):
        for key, value in schema.items():
            yield str(key)
            yield from _keys(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _keys(value)


def _blocked(client: TestClient, stage: DecideStage) -> str:
    _ = month_to_date(stage, UDI_350, UDI_350)
    response = ask(client, stage.writer, candidate("pay-1", UDI_100))
    assert response.status_code == 200, response.text
    return str(response.json()["decision_id"])


def test_the_client_decision_view_hides_rules_and_aggregates(
    client: TestClient, decide_stage: DecideStage
):
    schema_keys = {
        key.lower() for key in _keys(DecisionClientResponse.model_json_schema())
    }
    assert not [key for key in schema_keys for word in FORBIDDEN if word in key]
    decision_id = _blocked(client, decide_stage)
    reader = machine_headers(decide_stage.tenant, "transactions:read")

    response = client.get(f"{DECISIONS}/{decision_id}", headers=reader)

    assert response.status_code == 200, response.text
    with tenant_session(system_ctx(decide_stage.tenant_id)) as s:
        expected = decision_client_view(s, UUID(decision_id))
    assert response.json() == expected.model_dump(mode="json")
    assert set(response.json()) == {
        "decision_id",
        "external_id",
        "decision",
        "mode",
        "decided_at",
    }
    assert TIER_N1 not in response.text


def test_the_staff_view_shows_rules_aggregates_and_the_linked_transaction(
    client: TestClient, decide_stage: DecideStage
):
    decision_id = _blocked(client, decide_stage)
    admin = staff_headers(
        client, decide_stage.tenant, email="ops@acme.mx", roles=(Role.AUDITOR_RO,)
    )

    response = client.get(f"{DECISIONS}/{decision_id}", headers=admin)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["decision"], body["mode"], body["external_id"]) == (
        "BLOCK",
        "EVALUATED",
        "pay-1",
    )
    assert body["relationship_id"] == str(decide_stage.n1)
    assert body["rail"] == "CASH"
    [reason] = body["reasons"]
    assert reason["rule_code"] == TIER_N1
    assert reason["rule_version_id"] == str(decide_stage.stage.versions[TIER_N1])
    assert set(reason["detail"]) == {"month_in"}
    assert len(body["ruleset_hash"]) == 64
    assert body["actor"].startswith("api_client:")
    assert body["transaction"] is None


def test_another_tenant_gets_404(client: TestClient, decide_stage: DecideStage):
    decision_id = _blocked(client, decide_stage)
    other = machine_headers(
        new_tenant("globex", "admin@globex.mx"), "transactions:read"
    )

    response = client.get(f"{DECISIONS}/{decision_id}", headers=other)

    assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")


def test_an_api_key_without_the_read_scope_is_refused(
    client: TestClient, decide_stage: DecideStage
):
    decision_id = _blocked(client, decide_stage)
    writer_only = machine_headers(decide_stage.tenant, "transactions:write")

    response = client.get(f"{DECISIONS}/{decision_id}", headers=writer_only)

    assert (response.status_code, response.json()["code"]) == (403, "SCOPE_MISSING")
