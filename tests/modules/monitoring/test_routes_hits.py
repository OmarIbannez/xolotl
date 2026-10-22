"""The staff rule hits API (SP-04 spec §9.6, §15; plan 4b B12).

Hits are staff only (tipping-off, D20, PRV-502): an API key with every scope is
refused on each route and writes no audit row, and a client's transaction view
never mentions a hit. The queue and the detail are masked; the reveal records
``VIEW_PII`` on the hit for each non-empty group (``party``, ``counterparties``)
before the party and counterparty reveals record their own rows and decrypt.

The stage is B7's: tenant A with ``CASH_STRUCTURING`` and ``TIER_N1_MONTHLY_OPS``
active. An N3 customer's three near-threshold cash deposits, each with the same
counterparty, make one ``CASH_STRUCTURING`` hit (``MEDIUM``, ``ALERT``); an N1
customer's two 3,300 MXN deposits make one ``TIER_N1_MONTHLY_OPS`` hit (``HIGH``,
``BLOCK``).
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update

from tests.helpers import audit_events, everything_stored, new_tenant, system_ctx
from tests.modules.monitoring.rules.hook_support import (
    CASH,
    TIER_N1,
    Stage,
    add_relationship,
    build_stage,
    deposit,
    feed,
    hits,
    local,
)
from tests.modules.party.route_support import machine_headers, staff_headers
from xolotl.modules.monitoring import routes_hits
from xolotl.modules.monitoring.process.hooks import reset_processing_hooks
from xolotl.modules.monitoring.rules.models import RuleHit
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.audit import VIEW_PII
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.auth.principal import access_rule_of
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.ids import uuid7

HITS = "/v1/monitoring/hits"
N3 = "cust-n3"
N1 = "cust-n1"
NAME = "Juan Pérez"
ACCOUNT = "012180001234567890"
COUNTERPARTY = {"kind": "PERSON", "name": NAME, "account": ACCOUNT, "country": "MX"}
CLIENT_KEYS = {"id", "external_id", "occurred_at", "status", "late"}
# Scopes no route declares yet that an API key may still carry (spec §15).
EXTRA_SCOPES = ("rules:read", "monitoring:read", "hits:read", "captures:read")


@dataclass(frozen=True, slots=True)
class Scenario:
    stage: Stage
    structuring: RuleHit
    limit: RuleHit
    near: list[UUID]
    pesos: list[UUID]

    @property
    def tenant(self) -> CreatedTenant:
        return self.stage.tenant


@pytest.fixture
def scenario(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Scenario]:
    stage = build_stage(tenant, admin_engine, monkeypatch)
    _ = add_relationship(tenant, N3, "N3")
    _ = add_relationship(tenant, N1, "N1")
    near = feed(
        stage,
        [
            deposit(f"dep-{n}", local(day), amount, customer_ref=N3)
            | {"counterparty": COUNTERPARTY}
            for n, day, amount in (
                (1, 7, "3600.00"),
                (2, 8, "3700.00"),
                (3, 9, "3800.00"),
            )
        ],
    )
    pesos = feed(
        stage,
        [
            deposit(f"mxn-{n}", local(day), "3300.00", customer_ref=N1, currency="MXN")
            for n, day in ((1, 10), (2, 11))
        ],
    )
    [structuring] = hits(stage, CASH)
    [limit] = hits(stage, TIER_N1)
    yield Scenario(stage, structuring, limit, near, pesos)
    reset_processing_hooks()


def _admin(client: TestClient, scenario: Scenario) -> dict[str, str]:
    return staff_headers(
        client, scenario.tenant, email="ops@acme.mx", roles=(Role.ADMIN,)
    )


def _ids(response_json: dict[str, object]) -> list[str]:
    items = response_json["items"]
    assert isinstance(items, list)
    return [str(item["id"]) for item in items]


def _view_pii(tenant_id: UUID) -> list[tuple[str | None, str | None, object]]:
    return [
        (row.entity_type, row.entity_id, row.detail)
        for row in audit_events(tenant_id)
        if row.action == VIEW_PII
    ]


def _audit_count(tenant_id: UUID) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return s.scalar(select(func.count()).select_from(AuditEventRow)) or 0


def _hit_routes(scenario: Scenario) -> list[str]:
    hit_id = scenario.structuring.id
    return [
        HITS,
        f"{HITS}/{hit_id}",
        f"{HITS}/{hit_id}/reveal",
        f"{HITS}/{hit_id}/transactions",
        f"/v1/parties/{scenario.structuring.party_id}/hits",
    ]


def test_the_queue_filters_and_paginates(client: TestClient, scenario: Scenario):
    admin = _admin(client, scenario)
    structuring, limit = str(scenario.structuring.id), str(scenario.limit.id)

    queue = client.get(HITS, headers=admin)
    assert queue.status_code == 200, queue.text
    assert _ids(queue.json()) == [limit, structuring]
    first = queue.json()["items"][1]
    assert first["rule_code"] == CASH
    assert first["rule_version_id"] == str(scenario.stage.versions[CASH])
    assert first["typology"] == ["STRUCTURING"]
    assert (first["severity"], first["outcome"]) == ("MEDIUM", "ALERT")
    assert first["key_kind"] == "RELATIONSHIP"
    assert first["key"] == str(scenario.structuring.relationship_id)
    assert first["relationship_id"] == str(scenario.structuring.relationship_id)
    assert first["fired_count"] == 1
    assert first["first_transaction_id"] == str(scenario.near[2])
    assert first["decision_id"] is None
    assert first["cooldown_until"] is not None
    party = first["party"]
    assert party["id"] == str(scenario.structuring.party_id)
    assert party["display_name"] == "L.R."
    assert "Lucía" not in queue.text
    assert "Ramos" not in queue.text

    filters = {
        "severity=HIGH": [limit],
        "severity=MEDIUM": [structuring],
        f"rule_code={CASH}": [structuring],
        "typology=LIMIT": [limit],
        "typology=STRUCTURING": [structuring],
        "typology=VELOCITY": [],
        "outcome=BLOCK": [limit],
        f"party_id={scenario.structuring.party_id}": [structuring],
        f"transaction_id={scenario.near[0]}": [structuring],
        f"transaction_id={scenario.pesos[1]}": [limit],
        f"created_before={scenario.limit.created_at.isoformat()}": [structuring],
    }
    for query, expected in filters.items():
        response = client.get(HITS, params=dict([query.split("=", 1)]), headers=admin)
        assert response.status_code == 200, (query, response.text)
        assert _ids(response.json()) == expected, query

    page_one = client.get(HITS, params={"limit": 1}, headers=admin).json()
    assert _ids(page_one) == [limit]
    assert page_one["next_cursor"] is not None
    page_two = client.get(
        HITS, params={"limit": 1, "cursor": page_one["next_cursor"]}, headers=admin
    ).json()
    assert _ids(page_two) == [structuring]
    assert page_two["next_cursor"] is None
    assert not _view_pii(scenario.tenant.tenant_id)


def test_the_detail_shows_evidence_and_masked_transactions(
    client: TestClient, scenario: Scenario
):
    admin = _admin(client, scenario)
    hit = scenario.structuring
    response = client.get(f"{HITS}/{hit.id}", headers=admin)
    assert response.status_code == 200, response.text
    shown = response.json()
    assert shown["id"] == str(hit.id)
    evidence = shown["evidence"]
    assert evidence["aggregates"]["near_count"] == {
        "value": "3",
        "unit": None,
        "window": "P7D",
    }
    near_sum = evidence["aggregates"]["near_sum"]
    assert near_sum["unit"] == "USD/PRIOR_BANKING_DAY"
    assert abs(float(near_sum["value"]) - 11100) < 1
    assert evidence["params"] == {
        "threshold": "5000",
        "near_ratio": "0.70",
        "window": "P7D",
        "min_count": "3",
    }
    assert "efectivo" in evidence["explanation"]
    assert evidence["lists_hash"] == hit.evidence["lists_hash"]
    assert shown["sentence"] == evidence["explanation"]
    assert shown["assignment_id"] == str(hit.assignment_id)
    assert shown["policy_snapshot_id"] == str(hit.policy_snapshot_id)
    assert shown["definition_hash"] == hit.definition_hash.hex()
    assert shown["dedup_key"] == hit.dedup_key

    first, second, third = scenario.near
    transactions = shown["transactions"]
    assert [(t["transaction_id"], t["role"]) for t in transactions] == [
        (str(third), "TRIGGER"),
        (str(second), "WINDOW"),
        (str(first), "WINDOW"),
    ]
    trigger = transactions[0]
    assert (trigger["direction"], trigger["instrument"], trigger["rail"]) == (
        "IN",
        "CASH",
        "CASH",
    )
    assert (trigger["amount_minor"], trigger["currency"]) == (380000, "USD")
    counterparty = trigger["counterparty"]
    assert counterparty["kind"] == "PERSON"
    assert counterparty["has_name"] is True
    assert counterparty["has_account"] is True
    assert {t["counterparty"]["id"] for t in transactions} == {counterparty["id"]}
    for secret in (NAME, "Pérez", ACCOUNT, "Lucía", "Ramos"):
        assert secret not in response.text
    assert not _view_pii(scenario.tenant.tenant_id)

    limit = client.get(f"{HITS}/{scenario.limit.id}", headers=admin).json()
    assert [t["counterparty"] for t in limit["transactions"]] == [None, None]
    assert limit["evidence"]["aggregates"]


def test_the_reveal_records_view_pii_per_group(client: TestClient, scenario: Scenario):
    admin = _admin(client, scenario)
    hit = scenario.structuring
    tenant_id = scenario.tenant.tenant_id
    _ = client.get(f"{HITS}/{hit.id}", headers=admin)
    assert not _view_pii(tenant_id)

    response = client.get(f"{HITS}/{hit.id}/reveal", headers=admin)
    assert response.status_code == 200, response.text
    shown = response.json()
    assert shown["party"]["given_names"] == "Lucía"
    assert shown["party"]["first_surname"] == "Ramos"
    [(counterparty_id, revealed)] = shown["counterparties"].items()
    assert (revealed["name"], revealed["account"]) == (NAME, ACCOUNT)

    party_id = str(hit.party_id)
    assert _view_pii(tenant_id) == [
        ("rule_hit", str(hit.id), {"group": "party"}),
        (
            "party",
            party_id,
            {"group": "profile", "fields": ["given_names", "first_surname"]},
        ),
        ("rule_hit", str(hit.id), {"group": "counterparties", "count": 1}),
        ("counterparty", counterparty_id, {"field": "name"}),
        ("counterparty", counterparty_id, {"field": "account"}),
    ]

    limit = client.get(f"{HITS}/{scenario.limit.id}/reveal", headers=admin).json()
    assert limit["counterparties"] == {}
    groups = [
        detail
        for entity, entity_id, detail in _view_pii(tenant_id)
        if entity == "rule_hit" and entity_id == str(scenario.limit.id)
    ]
    assert groups == [{"group": "party"}]


def test_a_partys_hits_are_listed_on_the_party(client: TestClient, scenario: Scenario):
    admin = _admin(client, scenario)
    path = f"/v1/parties/{scenario.structuring.party_id}/hits"
    response = client.get(path, headers=admin)
    assert response.status_code == 200, response.text
    assert _ids(response.json()) == [str(scenario.structuring.id)]
    other = client.get(f"/v1/parties/{scenario.limit.party_id}/hits", headers=admin)
    assert _ids(other.json()) == [str(scenario.limit.id)]
    assert client.get(f"/v1/parties/{uuid7()}/hits", headers=admin).status_code == 404


def declared_scopes(app: FastAPI) -> set[str]:
    scopes: set[str] = set()
    for context in iter_route_contexts(app.routes):
        if not isinstance(context.original_route, APIRoute):
            continue
        pending = list(context.dependant.dependencies)
        while pending:
            sub = pending.pop()
            rule = access_rule_of(sub.call)
            if rule is not None and rule.scope is not None:
                scopes.add(rule.scope)
            pending.extend(sub.dependencies)
    return scopes


def test_hits_are_staff_only_and_hidden_from_api_clients(
    app: FastAPI, client: TestClient, scenario: Scenario
):
    tenant_id = scenario.tenant.tenant_id
    every_scope = machine_headers(
        scenario.tenant, *sorted(declared_scopes(app) | set(EXTRA_SCOPES))
    )
    before = _audit_count(tenant_id)
    for path in _hit_routes(scenario):
        response = client.get(path, headers=every_scope)
        assert response.status_code == 403, path
        assert response.json()["code"] == "ROLE_FORBIDDEN", path
        assert CASH not in response.text
    assert _audit_count(tenant_id) == before

    for txn_id in (scenario.near[2], scenario.pesos[1]):
        response = client.get(f"/v1/transactions/{txn_id}", headers=every_scope)
        assert response.status_code == 200
        assert set(response.json()) == CLIENT_KEYS
        for leaked in ("hit", CASH, TIER_N1, "STRUCTURING", "ALERT", "BLOCK"):
            assert leaked not in response.text
    listed = client.get(
        "/v1/transactions",
        params={"party_id": str(scenario.limit.party_id)},
        headers=every_scope,
    )
    for item in listed.json()["items"]:
        assert set(item) == CLIENT_KEYS
    assert _audit_count(tenant_id) == before


def test_auditor_reads_hits(client: TestClient, scenario: Scenario):
    auditor = staff_headers(
        client, scenario.tenant, email="audit@acme.mx", roles=(Role.AUDITOR_RO,)
    )
    for path in _hit_routes(scenario):
        response = client.get(path, headers=auditor)
        assert response.status_code == 200, (path, response.text)
    hit_id = str(scenario.structuring.id)
    assert [
        detail
        for entity, entity_id, detail in _view_pii(scenario.tenant.tenant_id)
        if entity == "rule_hit" and entity_id == hit_id
    ] == [{"group": "party"}, {"group": "counterparties", "count": 1}]


def test_another_tenant_sees_nothing(client: TestClient, scenario: Scenario):
    other = new_tenant("globex", "admin@globex.mx")
    theirs = staff_headers(client, other, email="ops@globex.mx", roles=(Role.ADMIN,))
    assert client.get(HITS, headers=theirs).json()["items"] == []
    filtered = client.get(
        HITS, params={"transaction_id": str(scenario.near[0])}, headers=theirs
    )
    assert filtered.json()["items"] == []
    for path in _hit_routes(scenario)[1:]:
        assert client.get(path, headers=theirs).status_code == 404, path
    assert not _view_pii(scenario.tenant.tenant_id)
    assert not _view_pii(other.tenant_id)


def test_no_plaintext_is_stored_beyond_the_encrypted_columns(
    admin_engine: Engine,
    caplog: pytest.LogCaptureFixture,
    client: TestClient,
    scenario: Scenario,
):
    caplog.set_level(logging.DEBUG)
    admin = _admin(client, scenario)
    for hit in (scenario.structuring, scenario.limit):
        assert client.get(f"{HITS}/{hit.id}", headers=admin).status_code == 200
        assert client.get(f"{HITS}/{hit.id}/reveal", headers=admin).status_code == 200
    dump = everything_stored(admin_engine)
    # The name indexes hold normalized tokens ("perez"), never the name as given.
    for secret in ("Lucía", "Ramos", NAME, "Pérez", ACCOUNT):
        assert secret not in dump, secret
        assert secret not in caplog.text, secret


def _transaction_ids(response_json: dict[str, object]) -> list[tuple[str, str]]:
    items = response_json["items"]
    assert isinstance(items, list)
    return [(str(item["transaction_id"]), str(item["role"])) for item in items]


def test_the_detail_caps_its_transactions_and_pages_the_rest(
    client: TestClient, scenario: Scenario, monkeypatch: pytest.MonkeyPatch
):
    admin = _admin(client, scenario)
    hit_id = scenario.structuring.id
    first, second, third = (str(txn_id) for txn_id in scenario.near)
    monkeypatch.setattr(routes_hits, "LINK_LIMIT", 2)
    shown = client.get(f"{HITS}/{hit_id}", headers=admin).json()
    assert shown["transaction_count"] == 3
    assert [t["transaction_id"] for t in shown["transactions"]] == [third, second]

    path = f"{HITS}/{hit_id}/transactions"
    page_one = client.get(path, params={"limit": 2}, headers=admin)
    assert page_one.status_code == 200, page_one.text
    assert _transaction_ids(page_one.json()) == [
        (third, "TRIGGER"),
        (second, "WINDOW"),
    ]
    cursor = page_one.json()["next_cursor"]
    assert cursor is not None
    page_two = client.get(
        path, params={"limit": 2, "cursor": cursor}, headers=admin
    ).json()
    assert _transaction_ids(page_two) == [(first, "WINDOW")]
    assert page_two["next_cursor"] is None
    assert (
        client.get(f"{HITS}/{uuid7()}/transactions", headers=admin).status_code == 404
    )


def test_the_reveal_covers_the_counterparties_of_the_shown_transactions(
    client: TestClient, scenario: Scenario, monkeypatch: pytest.MonkeyPatch
):
    admin = _admin(client, scenario)
    hit = scenario.structuring
    tenant_id = scenario.tenant.tenant_id
    # Only the trigger is shown, and it names no counterparty.
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            update(Transaction)
            .where(Transaction.id == scenario.near[2])
            .values(counterparty_id=None)
        )
    monkeypatch.setattr(routes_hits, "LINK_LIMIT", 1)
    shown = client.get(f"{HITS}/{hit.id}/reveal", headers=admin).json()
    assert shown["counterparties"] == {}
    assert [
        (entity, detail)
        for entity, _, detail in _view_pii(tenant_id)
        if entity != "party"
    ] == [("rule_hit", {"group": "party"})]


def test_a_failed_reveal_shows_nothing_and_keeps_no_audit_row(
    app: FastAPI, scenario: Scenario, monkeypatch: pytest.MonkeyPatch
):
    def broken(*_args: object) -> None:
        msg = "key unavailable"
        raise RuntimeError(msg)

    monkeypatch.setattr(routes_hits, "reveal_counterparty", broken)
    with TestClient(app, raise_server_exceptions=False) as client:
        admin = _admin(client, scenario)
        response = client.get(f"{HITS}/{scenario.structuring.id}/reveal", headers=admin)
    assert response.status_code == 500
    for secret in ("Lucía", "Ramos", NAME, ACCOUNT):
        assert secret not in response.text
    assert not _view_pii(scenario.tenant.tenant_id)
