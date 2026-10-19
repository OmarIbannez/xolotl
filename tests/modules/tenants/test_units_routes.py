"""The units and calendar routes: catalogue, lookups, values, holidays, deadlines."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.helpers import audit_events
from tests.modules.tenants.policy_support import (
    api_key_headers,
    holidays_seeded,
    staff_headers,
    unit_values_loaded,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.enums import Role

ROR_RULE = json.dumps({
    "kind": "NTH_BUSINESS_DAY_OF",
    "params": {"month_offset": 1, "n": 10},
    "tz": "America/Mexico_City",
})


@pytest.fixture
def loaded(admin_engine: Engine, engine: Engine) -> dict[tuple[str, date], int]:
    holidays_seeded(admin_engine)
    return unit_values_loaded(engine)


def test_the_catalogue_lists_every_unit_with_its_latest_value(
    client: TestClient, admin_headers, loaded
):
    resp = client.get("/v1/units", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    items = {item["unit_code"]: item for item in resp.json()["items"]}
    assert set(items) == {"UMA", "UDI", "USD_MXN_FIX"}
    assert items["UDI"]["latest_valid_from"] == "2026-01-21"
    assert items["UDI"]["series_id"] == "SP68257"
    assert items["UDI"]["latest_source"] == "fixture"
    assert items["UMA"]["daily"] is False


def test_a_lookup_resolves_the_rule_and_records_the_row(
    client: TestClient, admin_headers, loaded: dict[tuple[str, date], int]
):
    resp = client.get(
        "/v1/units/UDI",
        params={"on": "2026-01-20", "rule": "LAST_DAY_PREV_MONTH"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["resolved_date"], body["value"]) == ("2025-12-31", "8.52000000")
    assert body["unit_value_id"] == loaded["UDI", date(2025, 12, 31)]
    assert (body["on"], body["rule"], body["quote_ccy"]) == (
        "2026-01-20",
        "LAST_DAY_PREV_MONTH",
        "MXN",
    )


def test_a_missing_value_is_503_unit_value_missing(
    client: TestClient, admin_headers, loaded
):
    resp = client.get(
        "/v1/units/UDI",
        params={"on": "2026-02-05", "rule": "LAST_DAY_PREV_MONTH"},
        headers=admin_headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (503, "UNIT_VALUE_MISSING")
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_an_unknown_unit_is_422(client: TestClient, admin_headers, loaded):
    resp = client.get("/v1/units/TIIE", headers=admin_headers)
    assert resp.status_code == 422
    values = client.get("/v1/units/TIIE/values", headers=admin_headers)
    assert values.status_code == 422


def test_the_values_show_a_correction_beside_the_row_it_superseded(
    client: TestClient, admin_headers, loaded: dict[tuple[str, date], int]
):
    resp = client.get(
        "/v1/units/USD_MXN_FIX/values",
        params={"from": "2025-12-01", "to": "2025-12-31"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unit_code"] == "USD_MXN_FIX"
    eleventh = [item for item in body["items"] if item["valid_from"] == "2025-12-11"]
    assert len(eleventh) == 2
    newer, older = eleventh
    assert newer["superseded_by"] is None
    assert newer["value"] == "18.25000000"
    assert older["superseded_by"] == newer["id"]
    assert newer["id"] == loaded["USD_MXN_FIX", date(2025, 12, 11)]


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2025-01-01", "to": "2026-02-04"},
        {"from": "2026-01-02", "to": "2026-01-01"},
    ],
)
def test_a_span_over_366_days_or_backwards_is_422(
    client: TestClient, admin_headers, params: dict[str, str]
):
    resp = client.get("/v1/units/UDI/values", params=params, headers=admin_headers)
    assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_FAILED")


def test_the_holidays_of_a_scope_and_year(client: TestClient, admin_headers, loaded):
    resp = client.get(
        "/v1/calendar/holidays",
        params={"scope": "CNBV", "year": 2026},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["country"], body["scope"], body["year"]) == ("MX", "CNBV", 2026)
    assert {
        "day": "2026-04-02",
        "name": "Jueves Santo",
        "source": "CNBV 2026",
    } in body["items"]
    days = [item["day"] for item in body["items"]]
    assert "2026-05-05" not in days
    assert all(day.startswith("2026-") for day in days)
    assert days == sorted(days)


def test_the_quarterly_ror_falls_due_on_the_tenth_cnbv_business_day(
    client: TestClient, admin_headers, loaded
):
    resp = client.get(
        "/v1/calendar/due",
        params={
            "rule": ROR_RULE,
            "anchor": "2025-12-31T23:59:59-06:00",
            "scope": "CNBV",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["due_at"] == "2026-01-15T23:59:59-06:00"
    assert body["scope"] == "CNBV"
    assert body["rule"]["kind"] == "NTH_BUSINESS_DAY_OF"


@pytest.mark.parametrize(
    ("rule", "anchor"),
    [("nope", "2025-12-31T23:59:59-06:00"), (ROR_RULE, "2025-12-31T23:59:59")],
)
def test_a_bad_rule_or_a_naive_anchor_is_422(
    client: TestClient, admin_headers, rule: str, anchor: str
):
    resp = client.get(
        "/v1/calendar/due",
        params={"rule": rule, "anchor": anchor},
        headers=admin_headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_FAILED")


def test_any_staff_role_reads_and_api_keys_do_not(
    client: TestClient, tenant: CreatedTenant, admin_headers, loaded
):
    auditor = staff_headers(client, tenant, role=Role.AUDITOR_RO)
    assert client.get("/v1/units", headers=auditor).status_code == 200
    assert client.get("/v1/calendar/holidays", headers=auditor).status_code == 200
    machine = api_key_headers(tenant, scopes=["tenants:read"])
    for path in ("/v1/units", "/v1/units/UDI", "/v1/calendar/holidays"):
        resp = client.get(path, headers=machine)
        assert (resp.status_code, resp.json()["code"]) == (403, "ROLE_FORBIDDEN"), path
    # Reads record nothing.
    assert not [
        event
        for event in audit_events(tenant.tenant_id)
        if event.action.startswith(("unit", "calendar"))
    ]
