"""The screening API through the real application (SP-03 spec §9, §12; E7).

Staff get hits, findings and their explanations; API clients get status fields only
(FR-SC-013). The match queue, detail, reveal and dispositions are staff only, the
whitelist revoke the MLRO's, and the reveal is the one read that records
``VIEW_PII``. Another tenant sees none of it.
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, event, func, select

from tests.helpers import audit_events, leaked, new_tenant, staff_session
from tests.modules.party.conftest import payloads_of
from tests.modules.party.route_support import post
from tests.modules.screening.catalog.support import load_private
from tests.modules.screening.review.support import assign_itf
from tests.modules.screening.route_support import (
    AD_HOC,
    BLOCKED,
    CHECKS,
    LPB,
    MATCHES,
    NOBODY,
    OFAC,
    PERSON,
    RFC,
    SCREENING,
    api_client,
    check_party,
    create_party,
    dispose,
    seed_lists,
    seed_match,
    staff_client,
    stored_beyond_name_index,
)
from xolotl.modules.party.retention import purge_party
from xolotl.modules.party.service import get_party
from xolotl.modules.screening.api import match_summaries
from xolotl.modules.screening.engine.holder import get_index_holder
from xolotl.modules.screening.engine.models import ScreeningRun
from xolotl.modules.screening.errors import ScreeningIndexUnavailableError
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.audit import VIEW_PII
from xolotl.platform.enums import Role
from xolotl.platform.storage.local import LocalFsObjectStore

ALL_SCOPES = ("screening:read", "screening:write")
CLIENT_FORBIDDEN = {
    "list_code",
    "list_kind",
    "score",
    "band",
    "hits",
    "features",
    "findings",
    "entry",
    "open_finding_count",
    "explanation",
}


@pytest.fixture
def store(tmp_path: Path) -> LocalFsObjectStore:
    return LocalFsObjectStore(tmp_path / "objects")


@pytest.fixture
def screening(
    tenant: CreatedTenant, admin_engine: Engine, store: LocalFsObjectStore
) -> None:
    assign_itf(tenant, admin_engine)
    seed_lists(admin_engine, store)


def _run_count(tenant: CreatedTenant) -> int:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return s.scalar(select(func.count()).select_from(ScreeningRun)) or 0


# ---- checks ---------------------------------------------------------------------


def test_a_staff_party_check_returns_hits_with_explanations(
    client, tenant, admin_headers, screening
):
    party_id = create_party(client, admin_headers)

    body = check_party(client, admin_headers, party_id)

    assert body["status"] == "PENDING_REVIEW"
    [hit] = body["hits"]
    assert (hit["band"], hit["list_code"], hit["list_kind"]) == (
        "ALERT",
        OFAC,
        "SANCTION",
    )
    assert hit["features"]["alignment"]
    assert hit["match_id"] is not None
    run = client.get(f"{SCREENING}/runs/{body['run_id']}", headers=admin_headers)
    assert run.status_code == 200, run.text
    detail = run.json()
    assert detail["trigger_type"] == "CONSOLE"
    assert detail["mode"] == "AD_HOC"
    assert detail["party_id"] == party_id
    assert [m["list_code"] for m in detail["list_set"]] == [OFAC]
    assert [h["id"] for h in detail["hits"]] == [hit["id"]]
    assert detail["hit_count"] == 1
    assert detail["new_match_count"] == 1


def test_an_api_client_check_returns_status_only(
    client, tenant, admin_headers, screening
):
    party_id = create_party(client, admin_headers)
    machine = api_client(tenant, *ALL_SCOPES)

    checked = post(client, machine, CHECKS, {"party_id": party_id}, "k1")

    assert checked.status_code == 201, checked.text
    body = checked.json()
    assert set(body) == {"run_id", "subject_id", "status", "screened_at"}
    assert body["status"] == "PENDING_REVIEW"
    run = client.get(f"{SCREENING}/runs/{body['run_id']}", headers=machine)
    assert run.status_code == 200, run.text
    assert set(run.json()) == {
        "id",
        "subject_id",
        "party_id",
        "mode",
        "outcome",
        "created_at",
    }
    party = client.get(f"{SCREENING}/parties/{party_id}", headers=machine)
    assert party.status_code == 200, party.text
    assert party.json()["status"] == "PENDING_REVIEW"
    assert party.json()["lists_covered"] == [OFAC]
    assert "findings" not in party.json()
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        run_row = s.scalars(select(ScreeningRun)).one()
        assert run_row.trigger_type.value == "API"
        assert run_row.trigger_ref


def _properties(schemas: dict, schema: dict, seen: set[str]) -> set[str]:
    found: set[str] = set()
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name in seen:
            return found
        seen.add(name)
        return _properties(schemas, schemas[name], seen)
    for key, value in schema.get("properties", {}).items():
        found.add(key)
        found |= _properties(schemas, value, seen)
    for key in ("items", "additionalProperties"):
        if isinstance(schema.get(key), dict):
            found |= _properties(schemas, schema[key], seen)
    for key in ("anyOf", "oneOf", "allOf"):
        for each in schema.get(key, []):
            found |= _properties(schemas, each, seen)
    return found


def test_client_views_expose_status_only(app: FastAPI):
    schemas = app.openapi()["components"]["schemas"]
    for name in ("CheckResponse", "RunResponse", "PartyScreeningResponse"):
        names = _properties(schemas, schemas[name], set())
        assert names, name
        assert names.isdisjoint(CLIENT_FORBIDDEN), (name, names & CLIENT_FORBIDDEN)


def test_an_api_client_cannot_narrow_lists(client, tenant, admin_headers, screening):
    party_id = create_party(client, admin_headers)
    machine = api_client(tenant, *ALL_SCOPES)

    refused = post(
        client, machine, CHECKS, {"party_id": party_id, "lists": [OFAC]}, "k1"
    )

    assert (refused.status_code, refused.json()["code"]) == (422, "VALIDATION_FAILED")
    narrowed = post(
        client, admin_headers, CHECKS, {"party_id": party_id, "lists": ["UN"]}, "k2"
    )
    assert narrowed.status_code == 201, narrowed.text
    assert narrowed.json()["hits"] == []
    assert _run_count(tenant) == 1


def test_an_inline_subject_check_is_audited_without_its_input(
    client, tenant, admin_headers, screening, caplog
):
    caplog.set_level(logging.DEBUG)

    checked = post(client, admin_headers, CHECKS, {"subject": AD_HOC}, "k1")

    assert checked.status_code == 201, checked.text
    body = checked.json()
    assert [h["band"] for h in body["hits"]] == ["ALERT"]
    [event] = [
        e
        for e in audit_events(tenant.tenant_id)
        if e.action == "screening.check_requested"
    ]
    assert event.entity_type == "screening_subject"
    assert event.entity_id == body["subject_id"]
    assert event.detail == {"run_id": body["run_id"]}
    assert "Pérez" not in caplog.text
    assert "Juan Carlos" not in caplog.text


def test_a_check_needs_exactly_one_of_party_and_subject(
    client, tenant, admin_headers, screening
):
    party_id = create_party(client, admin_headers)
    for i, body in enumerate(({}, {"party_id": party_id, "subject": AD_HOC})):
        refused = post(client, admin_headers, CHECKS, body, f"k{i}")
        assert (refused.status_code, refused.json()["code"]) == (
            422,
            "VALIDATION_FAILED",
        )
    assert _run_count(tenant) == 0


def test_the_check_is_idempotent(client, tenant, admin_headers, screening):
    party_id = create_party(client, admin_headers)

    first = post(client, admin_headers, CHECKS, {"party_id": party_id}, "k1")
    again = post(client, admin_headers, CHECKS, {"party_id": party_id}, "k1")

    assert first.status_code == again.status_code == 201
    assert again.headers["Idempotent-Replay"] == "true"
    assert again.json()["run_id"] == first.json()["run_id"]
    assert _run_count(tenant) == 1


def test_the_party_view_for_a_never_screened_party_has_no_status(
    client, tenant, admin_headers, screening
):
    party_id = create_party(client, admin_headers)

    staff = client.get(f"{SCREENING}/parties/{party_id}", headers=admin_headers)
    machine = client.get(
        f"{SCREENING}/parties/{party_id}",
        headers=api_client(tenant, "screening:read"),
    )

    assert staff.status_code == machine.status_code == 200
    assert staff.json() == {
        "party_id": party_id,
        "status": None,
        "last_screened_at": None,
        "lists_covered": [],
        "open_finding_count": 0,
        "findings": [],
    }
    assert machine.json() == {
        "party_id": party_id,
        "status": None,
        "last_screened_at": None,
        "lists_covered": [],
    }
    unknown = client.get(f"{SCREENING}/parties/{uuid4()}", headers=admin_headers)
    assert (unknown.status_code, unknown.json()["code"]) == (404, "NOT_FOUND")


def test_the_staff_party_view_lists_the_findings(
    client, tenant, admin_headers, screening
):
    party_id, match_id = seed_match(client, admin_headers)

    view = client.get(f"{SCREENING}/parties/{party_id}", headers=admin_headers)

    assert view.status_code == 200, view.text
    body = view.json()
    assert body["status"] == "PENDING_REVIEW"
    assert body["open_finding_count"] == 1
    assert [f["id"] for f in body["findings"]] == [match_id]
    assert body["findings"][0]["party_id"] == party_id


# ---- the queue and the detail ---------------------------------------------------


def test_the_match_queue_filters_and_paginates(
    client, tenant, admin_headers, screening
):
    _, first = seed_match(client, admin_headers)
    ad_hoc = post(client, admin_headers, CHECKS, {"subject": AD_HOC}, "k1").json()
    [second] = [h["match_id"] for h in ad_hoc["hits"]]
    _ = check_party(client, admin_headers, create_party(client, admin_headers, NOBODY))

    page1 = client.get(f"{MATCHES}?limit=1", headers=admin_headers)
    assert page1.status_code == 200, page1.text
    assert [m["id"] for m in page1.json()["items"]] == [second]
    cursor = page1.json()["next_cursor"]
    page2 = client.get(f"{MATCHES}?limit=1&cursor={cursor}", headers=admin_headers)
    assert [m["id"] for m in page2.json()["items"]] == [first]
    assert page2.json()["next_cursor"] is None

    def ids(query: str) -> list[str]:
        listed = client.get(f"{MATCHES}?{query}", headers=admin_headers)
        assert listed.status_code == 200, listed.text
        return [m["id"] for m in listed.json()["items"]]

    assert ids("band=ALERT&status=OPEN") == [second, first]
    assert ids("band=LOW_CONFIDENCE") == []
    assert ids("status=ESCALATED") == []
    assert ids(f"list_code={OFAC}&list_kind=SANCTION") == [second, first]
    assert ids("list_kind=PEP") == []
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert ids(f"created_before={future.replace('+', '%2B')}") == [second, first]
    assert ids(f"created_before={past.replace('+', '%2B')}") == []


def test_the_match_queue_shows_each_subject_masked(
    client, tenant, admin_headers, screening
):
    party_id, party_match = seed_match(client, admin_headers)
    ad_hoc = post(client, admin_headers, CHECKS, {"subject": AD_HOC}, "k1").json()
    [ad_hoc_match] = [h["match_id"] for h in ad_hoc["hits"]]
    purged_id = create_party(
        client, admin_headers, PERSON.model_copy(update={"identifiers": []})
    )
    purged = check_party(client, admin_headers, purged_id)
    [purged_match] = [h["match_id"] for h in purged["hits"] if h["match_id"]]
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = purge_party(s, get_party(s, UUID(purged_id)))
    before = len(audit_events(tenant.tenant_id))

    listed = client.get(MATCHES, headers=admin_headers)

    assert listed.status_code == 200, listed.text
    subjects = {m["id"]: m["subject"] for m in listed.json()["items"]}
    assert subjects[party_match] == {
        "kind": "PARTY",
        "party_id": party_id,
        "entity_type": "PERSON",
        "initials": "J.C.P.G.",
        "identifiers": ["RFC ****0XY2"],
    }
    assert subjects[ad_hoc_match] == {
        "kind": "AD_HOC",
        "party_id": None,
        "entity_type": "PERSON",
        "initials": "J.C.P.G.",
        "identifiers": [],
    }
    assert subjects[purged_match] == {
        "kind": "PARTY",
        "party_id": purged_id,
        "entity_type": "PERSON",
        "initials": "",
        "identifiers": [],
    }
    assert RFC not in listed.text
    assert "Pérez" not in listed.text
    assert len(audit_events(tenant.tenant_id)) == before
    view = client.get(f"{SCREENING}/parties/{party_id}", headers=admin_headers)
    assert view.json()["findings"][0]["subject"] == subjects[party_match]


def test_an_empty_page_of_findings_issues_no_query(tenant, engine):
    # The party view of a never-screened party builds its rows from no findings.
    statements: list[str] = []

    def keep(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        event.listen(engine, "before_cursor_execute", keep)
        try:
            assert match_summaries(s, []) == []
        finally:
            event.remove(engine, "before_cursor_execute", keep)
    assert statements == []


def test_the_match_detail_is_masked_and_lists_the_allowed_actions(
    client, tenant, admin_headers, screening
):
    party_id, match_id = seed_match(client, admin_headers)
    analyst = staff_client(client, tenant, Role.ANALYST)
    before = len(audit_events(tenant.tenant_id))

    detail = client.get(f"{MATCHES}/{match_id}", headers=analyst)

    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["subject"] == {
        "kind": "PARTY",
        "party_id": party_id,
        "entity_type": "PERSON",
        "initials": "J.C.P.G.",
        "identifiers": ["RFC ****0XY2"],
    }
    assert RFC not in detail.text
    assert "Pérez" not in detail.text
    assert body["entry"]["primary_name"]["full"] == "JUAN CARLOS PEREZ GOMEZ"
    assert body["origin_hit"]["features"]["alignment"]
    assert body["latest_hit"]["id"] == body["origin_hit"]["id"]
    assert [h["hit_id"] for h in body["history"]] == [body["origin_hit"]["id"]]
    assert body["dispositions"] == []
    assert body["whitelist_id"] is None
    assert body["allowed_actions"] == ["FALSE_POSITIVE", "POSSIBLE_TRUE_MATCH"]
    assert body["blocked_reasons"] == {
        "TRUE_MATCH": ["STATE", "ROLE_REQUIRED"],
        "DELISTED": ["ROLE_REQUIRED", "NOT_DELISTED"],
    }
    assert len(audit_events(tenant.tenant_id)) == before
    missing = client.get(f"{MATCHES}/{uuid4()}", headers=analyst)
    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")


def test_the_reveal_records_view_pii_per_group(
    client, tenant, admin_headers, screening
):
    _, match_id = seed_match(client, admin_headers)
    rationale = "Fecha de nacimiento distinta en el pasaporte"
    mlro = staff_client(client, tenant, Role.MLRO)
    disposed = dispose(client, mlro, match_id, "FALSE_POSITIVE", rationale)
    assert disposed.status_code == 201, disposed.text
    _ = client.get(f"{MATCHES}/{match_id}", headers=admin_headers)
    before = [e for e in audit_events(tenant.tenant_id) if e.action == VIEW_PII]
    assert before == []

    revealed = client.get(f"{MATCHES}/{match_id}/reveal", headers=admin_headers)

    assert revealed.status_code == 200, revealed.text
    body = revealed.json()
    assert body["subject_names"] == ["Juan Carlos Pérez Gómez"]
    assert body["birth_date"] == "1970-05-10"
    assert body["identifiers"] == [{"id_type": "RFC", "value": RFC}]
    assert body["private_entry"] is None
    assert body["rationales"] == {disposed.json()["id"]: rationale}
    views = [e for e in audit_events(tenant.tenant_id) if e.action == VIEW_PII]
    assert [(e.entity_type, e.detail) for e in views] == [
        ("screening_subject", {"group": "subject"}),
        ("screening_match", {"group": "rationales", "count": 1}),
    ]


def test_a_private_list_finding_reveals_its_entry(
    client, tenant, admin_headers, screening, store
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id, roles=(Role.MLRO,)) as s:
        _ = load_private(s, store, LPB, [BLOCKED])
    party_id = create_party(client, admin_headers)
    body = check_party(client, admin_headers, party_id)
    [match_id] = [h["match_id"] for h in body["hits"] if h["list_code"] == LPB]

    detail = client.get(f"{MATCHES}/{match_id}", headers=admin_headers)
    revealed = client.get(f"{MATCHES}/{match_id}/reveal", headers=admin_headers)

    assert detail.status_code == revealed.status_code == 200
    assert detail.json()["entry"] is None
    assert detail.json()["list_kind"] == "BLOCKED"
    assert revealed.json()["private_entry"]["entry_key"] == "LPB-1"
    groups = [
        (e.detail or {}).get("group")
        for e in audit_events(tenant.tenant_id)
        if e.action == VIEW_PII
    ]
    assert groups == ["subject", "private_entry"]


# ---- dispositions and the whitelist ---------------------------------------------


def test_a_refused_disposition_is_a_409_with_blocked_reasons(
    client, tenant, admin_headers, screening
):
    _, match_id = seed_match(client, admin_headers)
    senior = staff_client(client, tenant, Role.SENIOR_ANALYST)
    escalated = dispose(client, senior, match_id, "POSSIBLE_TRUE_MATCH")
    assert escalated.status_code == 201, escalated.text
    assert escalated.json()["match_status"] == "ESCALATED"
    assert escalated.json()["decision"] == "POSSIBLE_TRUE_MATCH"

    refused = dispose(client, senior, match_id, "TRUE_MATCH")

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "DISPOSITION_NOT_ALLOWED"
    assert refused.json()["blocked_reasons"] == ["SAME_REVIEWER"]
    confirmed = dispose(
        client, staff_client(client, tenant, Role.MLRO), match_id, "TRUE_MATCH"
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["match_status"] == "TRUE_MATCH"
    short = dispose(client, senior, match_id, "FALSE_POSITIVE", "corto")
    assert (short.status_code, short.json()["code"]) == (422, "VALIDATION_FAILED")


def test_matches_dispositions_and_reveal_are_staff_only(
    client, tenant, admin_headers, screening
):
    _, match_id = seed_match(client, admin_headers)
    machine = api_client(tenant, *ALL_SCOPES)

    responses = [
        client.get(MATCHES, headers=machine),
        client.get(f"{MATCHES}/{match_id}", headers=machine),
        client.get(f"{MATCHES}/{match_id}/reveal", headers=machine),
        dispose(client, machine, match_id, "FALSE_POSITIVE"),
        client.delete(f"{SCREENING}/whitelist/{uuid4()}", headers=machine),
    ]

    assert [r.status_code for r in responses] == [403] * 5


def test_auditor_reads_but_never_writes(client, tenant, admin_headers, screening):
    party_id, match_id = seed_match(client, admin_headers)
    auditor = staff_client(client, tenant, Role.AUDITOR_RO)

    reads = [
        client.get(MATCHES, headers=auditor),
        client.get(f"{MATCHES}/{match_id}", headers=auditor),
        client.get(f"{SCREENING}/parties/{party_id}", headers=auditor),
    ]
    assert [r.status_code for r in reads] == [200] * 3
    assert reads[1].json()["allowed_actions"] == []
    writes = [
        dispose(client, auditor, match_id, "FALSE_POSITIVE"),
        post(client, auditor, CHECKS, {"party_id": party_id}, "k1"),
    ]
    assert [(r.status_code, r.json()["code"]) for r in writes] == [
        (403, "ROLE_FORBIDDEN")
    ] * 2


def test_the_whitelist_revoke_is_mlro_only(client, tenant, admin_headers, screening):
    _, match_id = seed_match(client, admin_headers)
    analyst = staff_client(client, tenant, Role.ANALYST)
    assert dispose(client, analyst, match_id, "FALSE_POSITIVE").status_code == 201
    whitelist_id = client.get(f"{MATCHES}/{match_id}", headers=analyst).json()[
        "whitelist_id"
    ]
    assert whitelist_id is not None
    path = f"{SCREENING}/whitelist/{whitelist_id}"

    refused = client.delete(path, headers=analyst)
    revoked = client.delete(path, headers=staff_client(client, tenant, Role.MLRO))

    assert (refused.status_code, refused.json()["code"]) == (403, "ROLE_FORBIDDEN")
    assert revoked.status_code == 204
    assert (
        client.get(f"{MATCHES}/{match_id}", headers=analyst).json()["whitelist_id"]
        is None
    )
    assert "screening.whitelist_revoked" in [
        e.action for e in audit_events(tenant.tenant_id)
    ]


# ---- isolation, availability and plaintext --------------------------------------


def test_another_tenant_sees_nothing(client, tenant, admin_headers, screening):
    party_id, match_id = seed_match(client, admin_headers)
    other = new_tenant("globex", "admin@globex.mx")
    theirs = staff_client(client, other, Role.MLRO)
    run = check_party(client, admin_headers, party_id)["run_id"]

    responses = [
        client.get(f"{SCREENING}/runs/{run}", headers=theirs),
        client.get(f"{MATCHES}/{match_id}", headers=theirs),
        client.get(f"{MATCHES}/{match_id}/reveal", headers=theirs),
        client.get(f"{SCREENING}/parties/{party_id}", headers=theirs),
    ]

    assert [(r.status_code, r.json()["code"]) for r in responses] == [
        (404, "NOT_FOUND")
    ] * 4
    listed = client.get(MATCHES, headers=theirs)
    assert listed.json()["items"] == []


def test_the_index_loading_is_a_503(
    client, tenant, admin_headers, screening, monkeypatch
):
    party_id = create_party(client, admin_headers)
    holder = get_index_holder()

    def loading(*_args: object, **_kwargs: object) -> object:
        raise ScreeningIndexUnavailableError

    monkeypatch.setattr(holder, "shared", loading)

    refused = post(client, admin_headers, CHECKS, {"party_id": party_id}, "k1")

    assert (refused.status_code, refused.json()["code"]) == (
        503,
        "SCREENING_INDEX_UNAVAILABLE",
    )
    assert _run_count(tenant) == 0


def test_no_plaintext_is_stored_or_logged_beyond_the_name_index(
    client, tenant, admin_headers, screening, admin_engine, caplog
):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="sqlalchemy.engine")
    rationale = f"Homonimo descartado {uuid4().hex[:10]}"
    _, match_id = seed_match(client, admin_headers)
    ad_hoc = post(client, admin_headers, CHECKS, {"subject": AD_HOC}, "k1")
    assert ad_hoc.status_code == 201, ad_hoc.text
    mlro = staff_client(client, tenant, Role.MLRO)
    disposed = dispose(client, mlro, match_id, "FALSE_POSITIVE", rationale)
    assert disposed.status_code == 201, disposed.text
    revealed = client.get(f"{MATCHES}/{match_id}/reveal", headers=admin_headers)
    assert revealed.status_code == 200

    stored = stored_beyond_name_index(admin_engine)
    payloads = payloads_of(tenant.tenant_id)
    for secret in ("Juan Carlos", "Pérez", "Gómez", RFC, rationale):
        assert leaked(caplog.text, secret.encode()) == [], secret
        assert leaked(stored, secret.encode()) == [], secret
        assert secret not in payloads
        assert secret not in stored
        assert secret not in caplog.text
