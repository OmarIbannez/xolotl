"""The staff captures API (SP-04 spec §10, §15; plan 4b B11).

Captures are staff only (tipping-off, spec §10.1, PRV-502): an API key with every
scope is refused on each route and a client's transaction view never mentions a
capture. The stage is B10's: a CRYPTO customer's five monthly MXN 4,000
operations and a December one cross 210 UMA on 11 December; an operation dated
10 December arrives late, so the group is superseded and its members are
captured again in a rebuilt group of seven.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.helpers import new_tenant
from tests.modules.monitoring.captures.support import (
    CaptureStage,
    build_stage,
    events,
    groups,
)
from tests.modules.monitoring.captures.test_supersede import (
    crossed_on_the_11th,
    send_late,
)
from tests.modules.party.route_support import machine_headers, post, staff_headers
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.captures.enums import CaptureGroupStatus
from xolotl.modules.monitoring.process.hooks import reset_processing_hooks
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.auth.principal import access_rule_of
from xolotl.platform.enums import Role

CAPTURES = "/v1/monitoring/captures"
PERIODS = f"{CAPTURES}/periods"
CODE = "AVI_XVI_A"
CLIENT_KEYS = {"id", "external_id", "occurred_at", "status", "late"}
# Scopes no route declares yet that an API key may still carry (spec §15).
EXTRA_SCOPES = ("rules:read", "monitoring:read", "hits:read", "captures:read")
NEW_YEAR = datetime(2027, 1, 1, 4, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)
MID_DECEMBER = datetime(2026, 12, 15, 12, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Scenario:
    stage: CaptureStage
    ids: list[UUID]
    late: UUID

    @property
    def tenant(self) -> CreatedTenant:
        return self.stage.tenant


@pytest.fixture
def scenario(tenant: CreatedTenant, admin_engine: Engine) -> Iterator[Scenario]:
    stage = build_stage(tenant, admin_engine)
    ids = crossed_on_the_11th(stage)
    late = send_late(stage)
    yield Scenario(stage, ids, late)
    reset_processing_hooks()


def at(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    monkeypatch.setattr(clock, "now", lambda: now)


def _ids(body: dict[str, object]) -> list[str]:
    items = body["items"]
    assert isinstance(items, list)
    return [str(item["id"]) for item in items]


def _admin(client: TestClient, scenario: Scenario) -> dict[str, str]:
    return staff_headers(
        client, scenario.tenant, email="ops@acme.mx", roles=(Role.ADMIN,)
    )


def test_captures_list_and_detail_with_group_and_chain(
    client: TestClient, scenario: Scenario
):
    admin = _admin(client, scenario)
    stored = events(scenario.stage, CODE)
    assert len(stored) == 7

    listed = client.get(CAPTURES, headers=admin)
    assert listed.status_code == 200, listed.text
    assert sorted(_ids(listed.json())) == sorted(str(e.id) for e in stored)
    for params, expected in (
        ({"capture_code": CODE}, 7),
        ({"capture_code": "ITF_ROR_CASH"}, 0),
        ({"report_code": "MX_AVISO_AVI"}, 7),
        ({"period_key": "2026-07"}, 1),
        ({"period_key": "2026-12"}, 2),
        ({"status": "CAPTURED"}, 7),
        ({"status": "VOIDED"}, 0),
        ({"relationship_id": str(stored[0].relationship_id)}, 7),
        ({"transaction_id": str(scenario.ids[0])}, 1),
    ):
        response = client.get(CAPTURES, params=params, headers=admin)
        assert response.status_code == 200, (params, response.text)
        assert len(response.json()["items"]) == expected, params
    first = client.get(CAPTURES, params={"limit": 4}, headers=admin).json()
    rest = client.get(
        CAPTURES, params={"limit": 4, "cursor": first["next_cursor"]}, headers=admin
    ).json()
    assert len(first["items"]) == 4
    assert rest["next_cursor"] is None
    assert len(set(_ids(first)) | set(_ids(rest))) == 7

    july = next(e for e in stored if e.transaction_id == scenario.ids[0])
    detail = client.get(f"{CAPTURES}/{july.id}", headers=admin)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["capture_code"] == CODE
    assert body["period_key"] == "2026-07"
    assert body["late"] is True
    assert body["fields"] == july.fields
    assert body["unit_values"] == july.unit_values
    assert body["supersedes_id"] is None
    assert body["superseded_by_id"] is None
    superseded, rebuilt = groups(scenario.stage)
    # Captured again in place into the group that replaced the first.
    assert superseded.superseded_by_id == rebuilt.id
    group = body["group"]
    assert group["id"] == str(rebuilt.id)
    assert group["status"] == CaptureGroupStatus.CROSSED.value
    assert group["member_count"] == 7
    assert group["superseded_by_id"] is None
    assert group["accumulated_minor"] == rebuilt.accumulated_minor
    assert client.get(f"{CAPTURES}/{scenario.late}", headers=admin).status_code == 404


def test_periods_are_listed_newest_first_with_their_counts(
    client: TestClient, scenario: Scenario
):
    admin = _admin(client, scenario)
    listed = client.get(PERIODS, headers=admin)
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert [item["period_key"] for item in items] == [
        "2026-12",
        "2026-11",
        "2026-10",
        "2026-09",
        "2026-08",
        "2026-07",
    ]
    # Counted now while open: the close keeps its own count.
    assert [item["event_count"] for item in items] == [2, 1, 1, 1, 1, 1]
    assert {item["status"] for item in items} == {"OPEN"}
    page = client.get(PERIODS, params={"limit": 4}, headers=admin).json()
    more = client.get(
        PERIODS, params={"limit": 4, "cursor": page["next_cursor"]}, headers=admin
    ).json()
    assert more["next_cursor"] is None
    assert [i["period_key"] for i in page["items"] + more["items"]] == [
        i["period_key"] for i in items
    ]
    by_code = client.get(PERIODS, params={"capture_code": "AVI_XVI_B"}, headers=admin)
    assert by_code.json()["items"] == []
    bad = client.get(PERIODS, params={"cursor": "nonsense"}, headers=admin)
    assert bad.status_code == 422


def test_a_manual_close_by_the_mlro(
    client: TestClient, scenario: Scenario, monkeypatch: pytest.MonkeyPatch
):
    # Tokens are signed on the real clock, so every member signs in first.
    roles = {
        "mlro": Role.MLRO,
        "analyst": Role.ANALYST,
        "audit": Role.AUDITOR_RO,
    }
    who = {
        name: staff_headers(
            client, scenario.tenant, email=f"{name}@acme.mx", roles=(role,)
        )
        for name, role in roles.items()
    }
    by_key = {
        item["period_key"]: item["id"]
        for item in client.get(PERIODS, headers=who["mlro"]).json()["items"]
    }
    at(monkeypatch, MID_DECEMBER)
    december = f"{PERIODS}/{by_key['2026-12']}/close"
    early = post(client, who["mlro"], december, {}, "close-0")
    assert early.status_code == 409, early.text
    assert early.json()["code"] == "PERIOD_NOT_ENDED"

    at(monkeypatch, NEW_YEAR)
    july = f"{PERIODS}/{by_key['2026-07']}/close"
    for name, key in (("analyst", "close-1"), ("audit", "close-2")):
        refused = post(client, who[name], july, {}, key)
        assert refused.status_code == 403, refused.text
    closed = post(client, who["mlro"], july, {}, "close-3")
    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert body["status"] == "CLOSED"
    assert body["event_count"] == body["reconciled_count"] == 1
    assert body["discrepancies"] is None
    only_closed = client.get(PERIODS, params={"status": "CLOSED"}, headers=who["audit"])
    assert _ids(only_closed.json()) == [by_key["2026-07"]]
    missing = post(client, who["mlro"], f"{PERIODS}/{scenario.late}/close", {}, "c-4")
    assert missing.status_code == 404


def test_effective_captures_carry_provenance(client: TestClient, scenario: Scenario):
    admin = _admin(client, scenario)
    itf = client.get(
        f"{CAPTURES}/effective",
        params={"on": "2026-12-15", "product": "EMONEY"},
        headers=admin,
    )
    assert itf.status_code == 200, itf.text
    rows = {row["code"]: row for row in itf.json()}
    assert set(rows) == {
        "ITF_ROR_CASH",
        "ITF_FX_CASH",
        "ITF_INTL_TRANSFER",
        "ITF_VA_OPS",
        "N1/monthly_ops",
        "N2/monthly_ops",
    }
    ror = rows["ITF_ROR_CASH"]
    assert (ror["kind"], ror["threshold"], ror["unit"]) == ("THRESHOLD", "5000", "USD")
    assert ror["conversion_rule"] == "PRIOR_BANKING_DAY"
    assert ror["period"] == "QUARTER"
    assert ror["report_code"] == "MX_F41_ROR"
    assert ror["legal_basis"].startswith("DCG art. 58 LRITF")
    assert ror["confidence"] == "VERIFIED"
    assert ror["source"] == "MX/ITF-IFPE"
    limit = rows["N1/monthly_ops"]
    assert (limit["kind"], limit["threshold"], limit["unit"]) == (
        "TIER_LIMIT",
        "750",
        "UDI",
    )
    assert limit["conversion_rule"] == "LAST_DAY_PREV_MONTH"
    assert limit["report_code"] is None

    avi = client.get(
        f"{CAPTURES}/effective",
        params={"on": "2026-12-15", "product": "CRYPTO"},
        headers=admin,
    ).json()
    accumulation = next(row for row in avi if row["code"] == CODE)
    assert accumulation["kind"] == "ACCUMULATION"
    assert accumulation["window"] == "P6M"
    assert accumulation["source"] == "MX/LFPIORPI-AVI"
    nothing = client.get(
        f"{CAPTURES}/effective", params={"on": "2025-01-01"}, headers=admin
    )
    assert nothing.status_code == 404
    assert nothing.json()["code"] == "POLICY_NOT_ASSIGNED"


def _declared_scopes(app: FastAPI) -> set[str]:
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


def test_captures_are_staff_only(app: FastAPI, client: TestClient, scenario: Scenario):
    every_scope = machine_headers(
        scenario.tenant, *sorted(_declared_scopes(app) | set(EXTRA_SCOPES))
    )
    (event, *_) = events(scenario.stage, CODE)
    admin = _admin(client, scenario)
    (period, *_) = client.get(PERIODS, headers=admin).json()["items"]
    for path in (
        CAPTURES,
        PERIODS,
        f"{CAPTURES}/effective?product=CRYPTO",
        f"{CAPTURES}/{event.id}",
    ):
        response = client.get(path, headers=every_scope)
        assert response.status_code == 403, path
        assert response.json()["code"] == "ROLE_FORBIDDEN", path
        assert CODE not in response.text
    refused = post(client, every_scope, f"{PERIODS}/{period['id']}/close", {}, "k-1")
    assert refused.status_code == 403
    for txn_id in scenario.ids:
        view = client.get(f"/v1/transactions/{txn_id}", headers=every_scope)
        assert view.status_code == 200
        assert set(view.json()) == CLIENT_KEYS
        assert "AVI" not in view.text


def test_another_tenant_sees_nothing(client: TestClient, scenario: Scenario):
    other = new_tenant("globex", "admin@globex.mx")
    theirs = staff_headers(client, other, email="ops@globex.mx", roles=(Role.ADMIN,))
    (event, *_) = events(scenario.stage, CODE)
    admin = _admin(client, scenario)
    (period, *_) = client.get(PERIODS, headers=admin).json()["items"]
    assert client.get(CAPTURES, headers=theirs).json()["items"] == []
    assert client.get(PERIODS, headers=theirs).json()["items"] == []
    assert client.get(f"{CAPTURES}/{event.id}", headers=theirs).status_code == 404
    closed = post(client, theirs, f"{PERIODS}/{period['id']}/close", {}, "k-2")
    assert closed.status_code == 404
