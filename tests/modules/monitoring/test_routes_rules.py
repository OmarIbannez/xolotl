"""The rules API: templates, rules, versions, lifecycle, simulations (plan 4b B9).

The library is loaded and the tenant starts with no rule. Simulations run over the
last week on the real clock (the tenant has no transaction, so they complete with
no hit) and activations start today, so every step is real: the job runs, the
lifecycle checks the simulation's age and hash, and the assignment is dated.

API clients read templates, rules and simulations with ``rules:read`` but never a
simulation's ``sample`` or ``overlap``, which say which customers a rule would hit
(tipping-off, PRV-502); every write and the assignments are staff only.
"""

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update

from tests.helpers import audit_events, new_tenant, system_ctx
from tests.modules.monitoring.route_support import (
    RULES,
    activate_rule,
    api_client,
    post,
    run_simulations,
    simulated,
    staff_client,
    today,
)
from tests.modules.monitoring.rules.lifecycle_support import load_library
from xolotl.modules.monitoring.rules import versions
from xolotl.modules.monitoring.rules.library import TYPOLOGIES_DIR, read_typology
from xolotl.modules.monitoring.rules.models import (
    MonitoringRuleVersion,
    RuleSimulation,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.config import get_settings
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role

TEMPLATES = "/v1/monitoring/templates"
SIMULATIONS = "/v1/monitoring/simulations"
ASSIGNMENTS = "/v1/monitoring/assignments"
CASH = "CASH_STRUCTURING"
GOLDEN = Path(__file__).resolve().parents[2] / "golden/monitoring/rules/render"
ACTIONS = ["EDIT", "SIMULATE", "SUBMIT", "APPROVE", "REJECT", "ACTIVATE", "RETIRE"]


@pytest.fixture
def library(admin_engine: Engine) -> None:
    load_library(admin_engine)


def _created(
    client: TestClient, headers: dict[str, str], code: str = CASH, **body: Any
) -> dict[str, Any]:
    response = post(client, headers, RULES, {"template_code": code, **body})
    assert response.status_code == 201, response.text
    return response.json()


def _version_path(version: dict[str, Any]) -> str:
    return f"{RULES}/{version['rule_id']}/versions/{version['version']}"


def _rule(client: TestClient, headers: dict[str, str], rule_id: str) -> dict[str, Any]:
    response = client.get(f"{RULES}/{rule_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _audit_count(tenant_id: UUID) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return s.scalar(select(func.count()).select_from(AuditEventRow)) or 0


def _actions(tenant_id: UUID) -> list[str]:
    return [row.action for row in audit_events(tenant_id)]


def test_the_library_lists_templates_with_bounds_and_both_sentences(
    client: TestClient, tenant: CreatedTenant, library: None
):
    analyst = staff_client(client, tenant, Role.ANALYST)
    response = client.get(TEMPLATES, headers=analyst)
    assert response.status_code == 200, response.text
    templates = response.json()
    assert len(templates) == 16
    codes = [template["code"] for template in templates]
    assert codes == sorted(codes)
    [cash] = [template for template in templates if template["code"] == CASH]
    assert (cash["family"], cash["kind"]) == ("ALERT", "STRUCTURING")
    assert cash["typology"] == ["STRUCTURING"]
    assert cash["country_code"] == "MX"
    assert cash["version"] == 1
    assert cash["packs"]
    params = {param["name"]: param for param in cash["params"]}
    threshold = params["threshold"]
    assert threshold["min"] == "1000"
    assert threshold["tighten"] == "LOWER"
    assert threshold["type"] == "DECIMAL"
    assert threshold["source"] == "template CASH_STRUCTURING v1"
    assert params["min_count"]["type"] == "INT"
    assert params["window"] == params["window"] | {"type": "STR", "value": "P7D"}
    golden = (GOLDEN / f"{CASH}.es-MX.txt").read_text(encoding="utf-8").strip()
    assert cash["sentences"]["es-MX"] == golden
    assert cash["sentences"]["en"]
    assert cash["definition"]["code"] == CASH

    one = client.get(f"{TEMPLATES}/{CASH}", headers=analyst)
    assert one.status_code == 200
    assert one.json() == cash
    unknown = client.get(f"{TEMPLATES}/NOPE", headers=analyst)
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "RULE_TEMPLATE_UNKNOWN"


def test_an_api_client_reads_templates_and_rules_with_the_scope(
    app: FastAPI, client: TestClient, tenant: CreatedTenant, library: None
):
    author = staff_client(client, tenant, Role.SENIOR_ANALYST)
    version = _created(client, author)
    simulation_id = simulated(client, app, author, version["rule_id"], 1)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = s.execute(
            update(RuleSimulation)
            .where(RuleSimulation.id == UUID(simulation_id))
            .values(
                sample=[{"party_id": "a-party-a-rule-would-hit"}],
                overlap={
                    "existing_hits": 1,
                    "same_customers": 1,
                    "later_true_match": 0,
                },
            )
        )
    staff_view = client.get(f"{SIMULATIONS}/{simulation_id}", headers=author).json()
    assert staff_view["sample"] == [{"party_id": "a-party-a-rule-would-hit"}]
    assert staff_view["overlap"]["existing_hits"] == 1

    reader = api_client(tenant, "rules:read")
    for path in (
        TEMPLATES,
        f"{TEMPLATES}/{CASH}",
        RULES,
        f"{RULES}/{version['rule_id']}",
        f"{SIMULATIONS}/{simulation_id}",
    ):
        response = client.get(path, headers=reader)
        assert response.status_code == 200, (path, response.text)
        assert "a-party-a-rule-would-hit" not in response.text, path
        assert "existing_hits" not in response.text, path
    shown = client.get(f"{SIMULATIONS}/{simulation_id}", headers=reader).json()
    assert (shown["sample"], shown["overlap"]) == ([], None)
    assert shown["status"] == "COMPLETED"
    rule = client.get(f"{RULES}/{version['rule_id']}", headers=reader).json()
    [shown_version] = rule["versions"]
    assert shown_version["allowed_actions"] == []
    assert shown_version["blocked_reasons"] == {}
    assert rule["last_simulation"]["sample"] == []

    unscoped = api_client(tenant, "transactions:read")
    response = client.get(RULES, headers=unscoped)
    assert response.status_code == 403
    assert response.json()["code"] == "SCOPE_MISSING"

    every = api_client(
        tenant, "rules:read", "monitoring:read", "transactions:read", "parties:write"
    )
    before = _audit_count(tenant.tenant_id)
    path = _version_path(version)
    for method, url, body in (
        ("POST", RULES, {"template_code": CASH}),
        ("POST", f"{RULES}/{version['rule_id']}/versions", {}),
        ("PATCH", path, {"severity": "HIGH"}),
        (
            "POST",
            f"{path}/simulate",
            {"range_start": "2026-01-01", "range_end": "2026-01-02"},
        ),
        ("POST", f"{path}/submit", {}),
        ("POST", f"{path}/reject", {"reason": "no"}),
        ("GET", ASSIGNMENTS, None),
    ):
        response = client.request(
            method, url, json=body, headers={**every, "Idempotency-Key": method + url}
        )
        assert response.status_code == 403, (method, url, response.text)
        assert response.json()["code"] == "ROLE_FORBIDDEN"
    assert _audit_count(tenant.tenant_id) == before


def test_a_senior_analyst_creates_tunes_and_simulates_a_rule(
    app: FastAPI, client: TestClient, tenant: CreatedTenant, library: None
):
    author = staff_client(client, tenant, Role.SENIOR_ANALYST)
    version = _created(client, author)
    assert (version["status"], version["version"]) == ("DRAFT", 1)
    assert version["simulation"] is None
    assert version["assignment"] is None
    assert "efectivo" in version["sentence"]
    assert version["dedup"] == {
        "key": "RELATIONSHIP",
        "window": "P7D",
        "cooldown": "P7D",
    }

    path = _version_path(version)
    patched = client.patch(
        path, json={"params": {"near_ratio": "0.75"}}, headers=author
    )
    assert patched.status_code == 200, patched.text
    tuned = patched.json()
    assert tuned["status"] == "DRAFT"
    near = next(param for param in tuned["params"] if param["name"] == "near_ratio")
    assert near["value"] == "0.75"
    assert tuned["definition_hash"] != version["definition_hash"]

    end = today()
    start = end.replace(day=1) if end.day > 1 else end
    accepted = post(
        client,
        author,
        f"{path}/simulate",
        {"range_start": start.isoformat(), "range_end": end.isoformat()},
    )
    assert accepted.status_code == 202, accepted.text
    simulation_id = accepted.json()["simulation_id"]
    queued = client.get(f"{SIMULATIONS}/{simulation_id}", headers=author).json()
    assert queued["status"] == "QUEUED"
    assert queued["valid_until"] is None
    assert queued["rule_version_id"] == version["id"]

    assert run_simulations(app) == 1
    done = client.get(f"{SIMULATIONS}/{simulation_id}", headers=author).json()
    assert done["status"] == "COMPLETED"
    assert done["valid_until"] is not None
    assert done["definition_hash"] == tuned["definition_hash"]
    assert (done["transactions_read"], done["hit_count"]) == (0, 0)
    assert done["range_end"] == end.isoformat()
    assert done["notes"] == []

    rule = _rule(client, author, version["rule_id"])
    [shown] = rule["versions"]
    assert shown["status"] == "SIMULATED"
    assert shown["simulation"]["id"] == simulation_id
    assert rule["last_simulation"]["id"] == simulation_id
    assert rule["active_version_id"] is None
    assert (rule["code"], rule["template_code"]) == (CASH, CASH)
    assert {
        "rules.version_created",
        "rules.version_updated",
        "rules.simulation_requested",
    } <= set(_actions(tenant.tenant_id))

    _assert_filters(client, author, version["rule_id"])
    too_long = post(
        client,
        author,
        f"{path}/simulate",
        {"range_start": "2020-01-01", "range_end": end.isoformat()},
    )
    assert too_long.status_code == 422
    assert too_long.json()["code"] == "SIMULATION_RANGE_TOO_LONG"


def _assert_filters(client: TestClient, headers: dict[str, str], rule_id: str) -> None:
    listed = client.get(RULES, headers=headers).json()
    assert [item["id"] for item in listed["items"]] == [rule_id]
    for query, expected in (
        ({"kind": "STRUCTURING"}, 1),
        ({"kind": "VELOCITY"}, 0),
        ({"typology": "STRUCTURING"}, 1),
        ({"typology": "MULE"}, 0),
        ({"status": "SIMULATED"}, 1),
        ({"status": "ACTIVE"}, 0),
    ):
        found = client.get(RULES, params=query, headers=headers).json()["items"]
        assert len(found) == expected, query


def test_out_of_bounds_params_are_a_422_with_the_source(
    client: TestClient, tenant: CreatedTenant, library: None
):
    author = staff_client(client, tenant, Role.SENIOR_ANALYST)
    response = post(
        client, author, RULES, {"template_code": CASH, "params": {"threshold": "500"}}
    )
    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["code"] == "RULE_PARAM_OUT_OF_BOUNDS"
    assert problem["errors"][0]["loc"] == ["params", "threshold"]
    assert "template CASH_STRUCTURING v1" in problem["errors"][0]["msg"]

    for body in (
        {"template_code": CASH, "params": {"near_ratio": 0.75}},
        {"template_code": CASH, "dedup": {"window": "P0D"}},
        {"template_code": CASH, "severity": "HUGE"},
    ):
        refused = post(client, author, RULES, body)
        assert refused.status_code == 422, (body, refused.text)
        assert refused.json()["code"] == "VALIDATION_FAILED"
    unknown = post(client, author, RULES, {"template_code": "NOPE"})
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "RULE_TEMPLATE_UNKNOWN"

    version = _created(client, author)
    patched = client.patch(
        _version_path(version), json={"params": {"threshold": "6000"}}, headers=author
    )
    assert patched.status_code == 422
    assert patched.json()["errors"][0]["loc"] == ["params", "threshold"]
    missing = client.patch(
        f"{RULES}/{version['rule_id']}/versions/9", json={}, headers=author
    )
    assert missing.status_code == 404


def test_the_lifecycle_through_the_api_with_two_users(
    app: FastAPI, client: TestClient, tenant: CreatedTenant, library: None
):
    author = staff_client(client, tenant, Role.MLRO)
    approver = staff_client(client, tenant, Role.MLRO)
    version = _created(client, author, segment={"tier": ["N2", "N3"]})
    assert version["segment"]["tier"] == ["N2", "N3"]
    path = _version_path(version)
    early = post(client, author, f"{path}/submit")
    assert early.status_code == 409
    assert early.json()["code"] == "RULE_TRANSITION_NOT_ALLOWED"
    assert early.json()["blocked_reasons"] == ["STATE", "SIMULATION_MISSING"]

    _ = simulated(client, app, author, version["rule_id"], 1)
    submitted = post(client, author, f"{path}/submit")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "IN_REVIEW"
    assert submitted.json()["submitted_at"] is not None

    own = post(client, author, f"{path}/approve")
    assert own.status_code == 409
    assert own.json()["blocked_reasons"] == ["SAME_AUTHOR"]

    approved = post(client, approver, f"{path}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    assert approved.json()["approved_by"] is not None

    past = post(client, approver, f"{path}/activate", {"effective_from": "2020-01-01"})
    assert past.status_code == 422
    assert past.json()["code"] == "VALIDATION_FAILED"
    activated = post(
        client, approver, f"{path}/activate", {"effective_from": today().isoformat()}
    )
    assert activated.status_code == 200, activated.text
    active = activated.json()
    assert active["status"] == "ACTIVE"
    assert active["activated_at"] is not None
    assignment = active["assignment"]
    assert assignment["effective_from"] == today().isoformat()
    assert assignment["effective_to"] is None
    assert assignment["product"] is None
    assert assignment["segment_filter"]["tier"] == ["N2", "N3"]
    assert assignment["rule_code"] == CASH
    assert assignment["rule_version_id"] == version["id"]

    _assert_assigned(client, author, assignment["id"])
    assert (
        _rule(client, author, version["rule_id"])["active_version_id"]
        == (version["id"])
    )

    retired = post(client, approver, f"{path}/retire")
    assert retired.status_code == 200, retired.text
    assert retired.json()["status"] == "RETIRED"
    assert retired.json()["assignment"]["effective_to"] == today().isoformat()
    assert {
        "rules.version_submitted",
        "rules.version_approved",
        "rules.version_activated",
        "rules.version_retired",
    } <= set(_actions(tenant.tenant_id))


def _assert_assigned(
    client: TestClient, headers: dict[str, str], assignment_id: str
) -> None:
    listed = client.get(
        ASSIGNMENTS, params={"on": today().isoformat()}, headers=headers
    )
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [assignment_id]
    assert client.get(ASSIGNMENTS, headers=headers).json() == listed.json()
    before = client.get(ASSIGNMENTS, params={"on": "2020-01-01"}, headers=headers)
    assert before.json() == []


def test_the_version_view_lists_allowed_actions_for_the_caller(
    app: FastAPI, client: TestClient, tenant: CreatedTenant, library: None
):
    author = staff_client(client, tenant, Role.SENIOR_ANALYST)
    version = _created(client, author)
    assert version["allowed_actions"] == ["EDIT", "SIMULATE"]
    assert version["blocked_reasons"]["SUBMIT"] == ["STATE", "SIMULATION_MISSING"]
    assert version["blocked_reasons"]["APPROVE"] == [
        "STATE",
        "ROLE_REQUIRED",
        "SAME_AUTHOR",
        "SIMULATION_MISSING",
    ]

    analyst = staff_client(client, tenant, Role.ANALYST)
    [seen] = _rule(client, analyst, version["rule_id"])["versions"]
    assert seen["allowed_actions"] == []
    assert sorted(seen["blocked_reasons"]) == sorted(ACTIONS)
    assert all(
        "ROLE_REQUIRED" in reasons for reasons in seen["blocked_reasons"].values()
    )

    # An MLRO who edits the version is one of its authors (four eyes extends to
    # editing): the view blocks their approval as the service does.
    editor = staff_client(client, tenant, Role.MLRO)
    path = _version_path(version)
    edited = client.patch(path, json={"severity": "HIGH"}, headers=editor)
    assert edited.status_code == 200, edited.text
    _ = simulated(client, app, author, version["rule_id"], 1)
    assert post(client, author, f"{path}/submit").status_code == 200
    [as_editor] = _rule(client, editor, version["rule_id"])["versions"]
    assert "APPROVE" not in as_editor["allowed_actions"]
    assert as_editor["blocked_reasons"]["APPROVE"] == ["SAME_AUTHOR"]
    refused = post(client, editor, f"{path}/approve")
    assert refused.json()["blocked_reasons"] == ["SAME_AUTHOR"]
    other = staff_client(client, tenant, Role.MLRO)
    [as_other] = _rule(client, other, version["rule_id"])["versions"]
    assert as_other["allowed_actions"] == ["APPROVE", "REJECT"]


def test_a_rejection_needs_a_reason_and_returns_to_draft(
    app: FastAPI, client: TestClient, tenant: CreatedTenant, library: None
):
    author = staff_client(client, tenant, Role.SENIOR_ANALYST)
    approver = staff_client(client, tenant, Role.MLRO)
    version = _created(client, author)
    path = _version_path(version)
    simulation_id = simulated(client, app, author, version["rule_id"], 1)
    assert post(client, author, f"{path}/submit").status_code == 200
    for body in ({}, {"reason": ""}, {"reason": "x" * 501}, {"reason": "   "}):
        refused = post(client, approver, f"{path}/reject", body)
        assert refused.status_code == 422, (body, refused.text)
        assert refused.json()["code"] == "VALIDATION_FAILED"
    reason = "El umbral es demasiado bajo para Juan"
    rejected = post(client, approver, f"{path}/reject", {"reason": reason})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "DRAFT"
    [row] = [
        row
        for row in audit_events(tenant.tenant_id)
        if row.action == "rules.version_rejected"
    ]
    assert row.detail == {"simulation_id": simulation_id, "reason_given": True}
    assert all(
        "Juan" not in str(event.detail) for event in audit_events(tenant.tenant_id)
    )


def test_a_short_profile_simulation_says_what_it_cannot_see(
    app: FastAPI, client: TestClient, tenant: CreatedTenant, library: None
):
    author = staff_client(client, tenant, Role.SENIOR_ANALYST)
    version = _created(client, author, "DECLARED_VS_ACTUAL_VOLUME")
    simulation_id = simulated(client, app, author, version["rule_id"], 1)
    shown = client.get(f"{SIMULATIONS}/{simulation_id}", headers=author).json()
    assert shown["status"] == "COMPLETED"
    assert shown["notes"] == ["PROFILE_HISTORY_SHORT"]
    missing = client.get(f"{SIMULATIONS}/{version['id']}", headers=author)
    assert missing.status_code == 404


def _custom_definition() -> dict[str, Any]:
    template = read_typology(TYPOLOGIES_DIR / "SELF_CUSTODY_OUTFLOW.yaml")
    data = template.definition.model_dump(mode="json")
    data["code"] = "MY_SELF_CUSTODY"
    return data


def _allow_custom(monkeypatch: pytest.MonkeyPatch, *, allowed: bool) -> None:
    settings = get_settings().model_copy(
        update={"monitoring_allow_custom_rules": allowed}
    )
    monkeypatch.setattr(versions, "get_settings", lambda: settings)


def test_custom_rules_need_the_flag_and_admin(
    client: TestClient,
    tenant: CreatedTenant,
    library: None,
    monkeypatch: pytest.MonkeyPatch,
):
    admin = staff_client(client, tenant, Role.ADMIN)
    senior = staff_client(client, tenant, Role.SENIOR_ANALYST)
    body = {"definition": _custom_definition()}
    disabled = post(client, admin, RULES, body)
    assert disabled.status_code == 422
    assert disabled.json()["code"] == "VALIDATION_FAILED"
    assert "custom rules are disabled" in disabled.json()["detail"]

    _allow_custom(monkeypatch, allowed=True)
    refused = post(client, senior, RULES, body)
    assert refused.status_code == 403
    assert refused.json()["code"] == "ROLE_FORBIDDEN"
    for wrong in (
        {},
        {"template_code": CASH, "definition": _custom_definition()},
        {"definition": _custom_definition(), "severity": "HIGH"},
        {"definition": {**_custom_definition(), "dedup": {"window": "P0D"}}},
        {"definition": {"code": "HALF"}},
    ):
        response = post(client, admin, RULES, wrong)
        assert response.status_code == 422, (wrong, response.text)
        assert response.json()["code"] == "VALIDATION_FAILED"
    created = post(client, admin, RULES, body)
    assert created.status_code == 201, created.text
    version = created.json()
    assert version["status"] == "DRAFT"
    assert version["params"][0]["source"] == "rule MY_SELF_CUSTODY v1"
    rule = _rule(client, admin, version["rule_id"])
    assert (rule["code"], rule["template_code"]) == ("MY_SELF_CUSTODY", None)


def test_writes_are_idempotent(
    client: TestClient, tenant: CreatedTenant, library: None
):
    author = staff_client(client, tenant, Role.SENIOR_ANALYST)
    first = post(client, author, RULES, {"template_code": CASH}, key="create-cash")
    again = post(client, author, RULES, {"template_code": CASH}, key="create-cash")
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        count = s.scalar(select(func.count()).select_from(MonitoringRuleVersion))
    assert count == 1
    reused = post(
        client, author, RULES, {"template_code": "BORDER_ZONE_CASH"}, key="create-cash"
    )
    assert reused.status_code == 422
    missing = client.post(RULES, json={"template_code": CASH}, headers=author)
    assert missing.status_code == 400
    assert missing.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_auditor_reads_but_never_writes(
    app: FastAPI, client: TestClient, tenant: CreatedTenant, library: None
):
    author = staff_client(client, tenant, Role.SENIOR_ANALYST)
    version = _created(client, author)
    auditor = staff_client(client, tenant, Role.AUDITOR_RO)
    assert client.get(RULES, headers=auditor).status_code == 200
    assert client.get(TEMPLATES, headers=auditor).status_code == 200
    assert client.get(ASSIGNMENTS, headers=auditor).status_code == 200
    [seen] = _rule(client, auditor, version["rule_id"])["versions"]
    assert seen["allowed_actions"] == []
    path = _version_path(version)
    before = _audit_count(tenant.tenant_id)
    for response in (
        post(client, auditor, RULES, {"template_code": CASH}),
        post(client, auditor, f"{RULES}/{version['rule_id']}/versions", {}),
        client.patch(path, json={"severity": "HIGH"}, headers=auditor),
        post(client, auditor, f"{path}/submit"),
        post(client, auditor, f"{path}/retire"),
    ):
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "ROLE_FORBIDDEN"
    assert _audit_count(tenant.tenant_id) == before


def test_another_tenant_sees_nothing(
    app: FastAPI, client: TestClient, tenant: CreatedTenant, library: None
):
    author = staff_client(client, tenant, Role.MLRO)
    approver = staff_client(client, tenant, Role.MLRO)
    active = activate_rule(client, app, author=author, approver=approver, code=CASH)
    rule = _rule(client, author, str(active["rule_id"]))
    simulation_id = rule["last_simulation"]["id"]

    other = new_tenant("other", "admin@other.mx")
    intruder = staff_client(client, other, Role.ADMIN)
    before = (_audit_count(tenant.tenant_id), _audit_count(other.tenant_id))
    assert client.get(RULES, headers=intruder).json()["items"] == []
    assert client.get(ASSIGNMENTS, headers=intruder).json() == []
    path = f"{RULES}/{active['rule_id']}/versions/{active['version']}"
    for response in (
        client.get(f"{RULES}/{active['rule_id']}", headers=intruder),
        client.get(f"{SIMULATIONS}/{simulation_id}", headers=intruder),
        post(client, intruder, f"{RULES}/{active['rule_id']}/versions", {}),
        client.patch(path, json={"severity": "HIGH"}, headers=intruder),
        post(
            client,
            intruder,
            f"{path}/simulate",
            {"range_start": today().isoformat(), "range_end": today().isoformat()},
        ),
        post(client, intruder, f"{path}/retire"),
    ):
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "NOT_FOUND"
    assert (_audit_count(tenant.tenant_id), _audit_count(other.tenant_id)) == before
    # The library is shared: the other tenant reads it and starts its own rule.
    assert len(client.get(TEMPLATES, headers=intruder).json()) == 16
