"""The private-list API through the real application (SP-03 spec §7, §12).

MLRO and ADMIN, and API keys with the ``private_lists`` scopes, create lists and
upload versions as raw CSV or XLSX bodies; a bad row answers 422 with located row
errors that never carry a cell. Only MLRO and ADMIN staff read entries, and each page
shown records ``VIEW_PII`` and ``private_list.entries_viewed`` first. Another tenant
sees none of it.
"""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response

from tests.helpers import audit_events, first_login, new_tenant
from tests.modules.party.route_support import (
    analyst_headers,
    machine_headers,
    mlro_headers,
    post,
    staff_headers,
)
from xolotl.modules.screening.catalog.template import CSV_TYPE, XLSX_TYPE
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.config import get_settings
from xolotl.platform.enums import Role
from xolotl.platform.storage.local import LocalFsObjectStore

PRIVATE_LISTS = "/v1/private-lists"
PRIVATE = Path(__file__).parent / "fixtures" / "private"
V1 = (PRIVATE / "lpb_v1.csv").read_bytes()
V2 = (PRIVATE / "lpb_v2.csv").read_bytes()
BAD = (PRIVATE / "bad_rows.csv").read_bytes()
LPB_BODY = {"list_code": "MX_LPB", "title": "Lista de personas bloqueadas"}
# Cells of bad_rows.csv a refusal must never echo.
BAD_CELLS = (
    "Teodomiro",
    "Cuauhtlequetzqui",
    "PX",
    "1980-13-45",
    "ZZZZ999999ZZZ",
    "NOTACURP1234567890",
    "FCO010203XY7",
    "MEXICO",
    "secreto de la empresa",
    "ayer",
    "Leocadio",
)


@pytest.fixture
def object_store(app: FastAPI, tmp_path: Path) -> LocalFsObjectStore:
    store = LocalFsObjectStore(tmp_path / "objects")
    app.state.xolotl.object_store = store
    return store


def _upload(
    client: TestClient,
    headers: dict[str, str],
    list_id: str,
    key: str,
    data: bytes = V1,
    content_type: str = CSV_TYPE,
) -> Response:
    return client.post(
        f"{PRIVATE_LISTS}/{list_id}/versions",
        content=data,
        headers={**headers, "Content-Type": content_type, "Idempotency-Key": key},
    )


def _create(client: TestClient, headers: dict[str, str], key: str = "c1") -> str:
    created = post(client, headers, PRIVATE_LISTS, LPB_BODY, key)
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _actions(tenant: CreatedTenant) -> list[str]:
    return [e.action for e in audit_events(tenant.tenant_id)]


def test_mlro_creates_and_lists_private_lists(client, tenant, object_store):
    _ = object_store
    mlro = mlro_headers(client, tenant)
    created = post(client, mlro, PRIVATE_LISTS, LPB_BODY, "c1")
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["list_code"], body["list_kind"]) == ("MX_LPB", "BLOCKED")
    assert body["title"] == LPB_BODY["title"]
    assert body["active_version"] is None
    replayed = post(client, mlro, PRIVATE_LISTS, LPB_BODY, "c1")
    assert replayed.status_code == 201
    assert replayed.headers["Idempotent-Replay"] == "true"
    assert replayed.json() == body
    duplicate = post(client, mlro, PRIVATE_LISTS, LPB_BODY, "c2")
    assert (duplicate.status_code, duplicate.json()["code"]) == (
        409,
        "PRIVATE_LIST_EXISTS",
    )
    internal = post(
        client,
        mlro,
        PRIVATE_LISTS,
        {"list_code": "INTERNAL_PEP", "title": "PEP", "list_kind": "PEP"},
        "c3",
    )
    assert internal.status_code == 201, internal.text
    bad_code = post(
        client, mlro, PRIVATE_LISTS, {"list_code": "OTHER", "title": "x"}, "c4"
    )
    assert bad_code.status_code == 422
    listed = client.get(PRIVATE_LISTS, headers=mlro)
    assert listed.status_code == 200, listed.text
    assert sorted(i["list_code"] for i in listed.json()["items"]) == [
        "INTERNAL_PEP",
        "MX_LPB",
    ]
    assert listed.json()["next_cursor"] is None
    assert _actions(tenant).count("private_list.created") == 2


def test_an_analyst_and_an_auditor_cannot_reach_private_lists(
    client, tenant, admin_headers, object_store
):
    _ = object_store
    list_id = _create(client, admin_headers)
    auditor = staff_headers(
        client, tenant, email="auditor@acme.mx", roles=(Role.AUDITOR_RO,)
    )
    for headers in (analyst_headers(client, tenant), auditor):
        responses = [
            post(client, headers, PRIVATE_LISTS, LPB_BODY, "x1"),
            client.get(PRIVATE_LISTS, headers=headers),
            _upload(client, headers, list_id, "x2"),
            client.get(f"{PRIVATE_LISTS}/{list_id}/versions", headers=headers),
            client.get(f"{PRIVATE_LISTS}/{list_id}/entries", headers=headers),
        ]
        assert [r.status_code for r in responses] == [403] * 5
        assert {r.json()["code"] for r in responses} == {"ROLE_FORBIDDEN"}


def test_an_api_key_with_the_scope_uploads_and_without_it_is_refused(
    client, tenant, admin_headers, object_store
):
    _ = object_store
    list_id = _create(client, admin_headers)
    writer = machine_headers(tenant, "private_lists:write")
    uploaded = _upload(client, writer, list_id, "m1")
    assert uploaded.status_code == 201, uploaded.text
    reader = machine_headers(tenant, "private_lists:read")
    refused = _upload(client, reader, list_id, "m2")
    assert (refused.status_code, refused.json()["code"]) == (403, "SCOPE_MISSING")
    assert client.get(PRIVATE_LISTS, headers=reader).status_code == 200
    versions = client.get(f"{PRIVATE_LISTS}/{list_id}/versions", headers=reader)
    assert versions.status_code == 200
    assert client.get(PRIVATE_LISTS, headers=writer).status_code == 403


def test_an_upload_returns_the_version_and_history_lists_it(
    client, tenant, object_store
):
    _ = object_store
    mlro = mlro_headers(client, tenant)
    list_id = _create(client, mlro)
    first = _upload(client, mlro, list_id, "u1")
    assert first.status_code == 201, first.text
    v1 = first.json()
    assert (v1["seq"], v1["status"], v1["entry_count"]) == (1, "ACTIVE", 3)
    assert (v1["added"], v1["changed"], v1["removed"]) == (3, 0, 0)
    assert v1["content_type"] == CSV_TYPE
    assert v1["file_size"] == len(V1)
    replayed = _upload(client, mlro, list_id, "u1")
    assert replayed.json() == v1
    second = _upload(
        client,
        mlro,
        list_id,
        "u2",
        (PRIVATE / "lpb_v1.xlsx").read_bytes(),
        f"{XLSX_TYPE}; charset=binary",
    )
    assert second.status_code == 201, second.text
    third = _upload(client, mlro, list_id, "u3", V2)
    v3 = third.json()
    assert (v3["seq"], v3["added"], v3["changed"], v3["removed"]) == (3, 1, 1, 1)
    history = client.get(
        f"{PRIVATE_LISTS}/{list_id}/versions", headers=mlro, params={"limit": 2}
    ).json()
    assert [(v["seq"], v["status"]) for v in history["items"]] == [
        (3, "ACTIVE"),
        (2, "SUPERSEDED"),
    ]
    rest = client.get(
        f"{PRIVATE_LISTS}/{list_id}/versions",
        headers=mlro,
        params={"cursor": history["next_cursor"]},
    ).json()
    assert [v["seq"] for v in rest["items"]] == [1]
    listed = client.get(PRIVATE_LISTS, headers=mlro).json()["items"]
    assert listed[0]["active_version"]["id"] == v3["id"]


def test_an_invalid_upload_answers_row_errors_without_values(
    client, tenant, admin_headers, object_store
):
    _ = object_store
    list_id = _create(client, admin_headers)
    refused = _upload(client, admin_headers, list_id, "b1", BAD)
    assert refused.status_code == 422, refused.text
    body = refused.json()
    assert body["code"] == "PRIVATE_LIST_INVALID"
    assert len(body["errors"]) == 10
    for error in body["errors"]:
        assert error["loc"][0] == "row"
        assert isinstance(error["loc"][1], int)
        assert isinstance(error["loc"][2], str)
    assert ["row", 2, "tipo"] in [e["loc"] for e in body["errors"]]
    for cell in BAD_CELLS:
        assert cell not in refused.text, cell
    history = client.get(f"{PRIVATE_LISTS}/{list_id}/versions", headers=admin_headers)
    assert history.json()["items"] == []
    retried = _upload(client, admin_headers, list_id, "b1", V1)
    assert retried.status_code == 201


def test_an_unsupported_content_type_or_oversize_body_is_refused(
    client, tenant, admin_headers, object_store, monkeypatch
):
    _ = object_store
    list_id = _create(client, admin_headers)
    pdf = _upload(client, admin_headers, list_id, "t1", V1, "application/pdf")
    assert (pdf.status_code, pdf.json()["code"]) == (422, "VALIDATION_FAILED")
    empty = _upload(client, admin_headers, list_id, "t2", b"")
    assert empty.status_code == 422
    monkeypatch.setattr(get_settings(), "private_list_max_mb", 1)
    large = _upload(client, admin_headers, list_id, "t3", V1 + b" " * (1024 * 1024))
    assert (large.status_code, large.json()["code"]) == (422, "VALIDATION_FAILED")
    assert "too large" in large.json()["detail"]
    missing = _upload(
        client, admin_headers, "00000000-0000-0000-0000-000000000001", "t4"
    )
    assert missing.status_code == 404


def test_entries_are_revealed_to_staff_with_view_pii_and_an_audit_row(
    client, tenant, object_store
):
    _ = object_store
    mlro = mlro_headers(client, tenant)
    list_id = _create(client, mlro)
    empty = client.get(f"{PRIVATE_LISTS}/{list_id}/entries", headers=mlro)
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "next_cursor": None}
    assert _upload(client, mlro, list_id, "u1").status_code == 201
    before = len(audit_events(tenant.tenant_id))
    first = client.get(
        f"{PRIVATE_LISTS}/{list_id}/entries", headers=mlro, params={"limit": 2}
    )
    assert first.status_code == 200, first.text
    page = first.json()
    assert len(page["items"]) == 2
    assert page["next_cursor"] is not None
    rest = client.get(
        f"{PRIVATE_LISTS}/{list_id}/entries",
        headers=mlro,
        params={"cursor": page["next_cursor"]},
    ).json()
    items = page["items"] + rest["items"]
    assert len(items) == 3
    assert rest["next_cursor"] is None
    by_key = {item["source_entry_key"]: item for item in items}
    ximena = by_key["LPB-001"]
    assert ximena["entity_type"] == "PERSON"
    assert ximena["entry"]["entry_key"] == "LPB-001"
    assert "Ximena" in json_text(ximena["entry"])
    assert "Observacion confidencial" not in first.text + json_text(rest)
    events = audit_events(tenant.tenant_id)[before:]
    assert [(e.action, e.entity_type, e.entity_id) for e in events] == [
        ("VIEW_PII", "private_list", list_id),
        ("private_list.entries_viewed", "private_list", list_id),
    ] * 2
    assert events[0].detail == {"field": "entries"}
    assert events[1].detail == {"list_id": list_id, "count": 2}
    assert events[3].detail == {"list_id": list_id, "count": 1}


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def test_an_api_client_never_reads_entries(client, tenant, admin_headers, object_store):
    _ = object_store
    list_id = _create(client, admin_headers)
    assert _upload(client, admin_headers, list_id, "u1").status_code == 201
    reader = machine_headers(tenant, "private_lists:read", "private_lists:write")
    refused = client.get(f"{PRIVATE_LISTS}/{list_id}/entries", headers=reader)
    assert (refused.status_code, refused.json()["code"]) == (403, "ROLE_FORBIDDEN")


def test_tenant_b_gets_not_found_for_tenant_a_lists(
    client, tenant, admin_headers, object_store
):
    _ = object_store
    list_id = _create(client, admin_headers)
    assert _upload(client, admin_headers, list_id, "u1").status_code == 201
    other = new_tenant("beta", "admin@beta.mx")
    beta = first_login(
        client, "beta", "admin@beta.mx", other.admin_password, "beta-admin-password-x"
    )
    assert client.get(PRIVATE_LISTS, headers=beta).json()["items"] == []
    for response in [
        client.get(f"{PRIVATE_LISTS}/{list_id}/versions", headers=beta),
        client.get(f"{PRIVATE_LISTS}/{list_id}/entries", headers=beta),
        _upload(client, beta, list_id, "b1"),
    ]:
        assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")
    # Tenant B may create its own MX_LPB: the code is unique per tenant only.
    assert post(client, beta, PRIVATE_LISTS, LPB_BODY, "c1").status_code == 201
