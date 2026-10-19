"""Party documents and legal hold through the real application.

Uploads are raw bodies stored in the local object store; only staff download
them, and each download records ``VIEW_PII`` on the document. The legal hold is
the MLRO's alone.
"""

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from tests.conftest import ADMIN_PASSWORD
from tests.helpers import audit_events, first_login, new_tenant
from tests.modules.party.route_support import (
    ANA_BODY,
    machine_headers,
    mlro_headers,
    post,
)
from xolotl.modules.party import documents
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.storage.local import LocalFsObjectStore

PARTIES = "/v1/parties"
PDF = b"%PDF-1.4 smoke"


def _upload(
    client: TestClient,
    headers: dict[str, str],
    party_id: str,
    key: str,
    *,
    data: bytes = PDF,
    content_type: str = "application/pdf",
) -> Response:
    return client.post(
        f"{PARTIES}/{party_id}/documents",
        params={"doc_type": "IDENTITY"},
        content=data,
        headers={**headers, "Content-Type": content_type, "Idempotency-Key": key},
    )


def _party(client: TestClient, headers: dict[str, str]) -> str:
    created = post(client, headers, PARTIES, ANA_BODY, "p1")
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [
        path for path in root.rglob("*") if path.is_file() and ".tmp" not in path.parts
    ]


def test_an_upload_is_stored_with_its_digest_and_listed_in_the_staff_detail(
    object_store: LocalFsObjectStore,
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    tmp_path: Path,
):
    _ = object_store
    party_id = _party(client, admin_headers)
    uploaded = _upload(client, admin_headers, party_id, "d1")
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert body["size"] == len(PDF)
    assert body["content_type"] == "application/pdf"
    assert body["doc_type"] == "IDENTITY"
    stored = (
        tmp_path / "objects" / str(tenant.tenant_id) / f"documents/{party_id}"
    ) / body["id"]
    assert stored.read_bytes() == PDF
    detail = client.get(f"{PARTIES}/{party_id}", headers=admin_headers).json()
    assert detail["documents"] == [body]
    machine = machine_headers(tenant, "parties:write")
    by_machine = _upload(client, machine, party_id, "m1")
    assert by_machine.status_code == 201, by_machine.text
    replayed = _upload(client, machine, party_id, "m1")
    assert replayed.status_code == 201
    assert replayed.headers["Idempotent-Replay"] == "true"
    assert replayed.json()["id"] == by_machine.json()["id"]
    detail = client.get(f"{PARTIES}/{party_id}", headers=admin_headers).json()
    assert len(detail["documents"]) == 2


def test_refusals_are_422(
    object_store: LocalFsObjectStore,
    client: TestClient,
    admin_headers,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _ = object_store
    party_id = _party(client, admin_headers)
    refusals = [
        ("text/plain", PDF, "unsupported document content type"),
        ("application/pdf", b"", "empty document"),
    ]
    for index, (content_type, data, detail) in enumerate(refusals):
        refused = _upload(
            client,
            admin_headers,
            party_id,
            f"r{index}",
            data=data,
            content_type=content_type,
        )
        assert (
            refused.status_code,
            refused.json()["code"],
            refused.json()["detail"],
        ) == (422, "VALIDATION_FAILED", detail)
    monkeypatch.setattr(documents, "MAX_DOCUMENT_BYTES", 8)
    large = _upload(client, admin_headers, party_id, "big")
    assert (large.status_code, large.json()["code"], large.json()["detail"]) == (
        422,
        "VALIDATION_FAILED",
        "document too large",
    )
    assert _files(tmp_path / "objects") == []


def test_the_download_records_view_pii_on_the_document(
    object_store: LocalFsObjectStore,
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
):
    _ = object_store
    party_id = _party(client, admin_headers)
    document_id = _upload(client, admin_headers, party_id, "d1").json()["id"]
    content = f"{PARTIES}/{party_id}/documents/{document_id}/content"
    downloaded = client.get(content, headers=admin_headers)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == PDF
    assert downloaded.headers["content-type"] == "application/pdf"
    assert downloaded.headers["content-disposition"] == (
        f'attachment; filename="{document_id}.pdf"'
    )
    views = [e for e in audit_events(tenant.tenant_id) if e.action == "VIEW_PII"]
    assert [(e.entity_type, e.entity_id, e.detail) for e in views] == [
        ("document", document_id, {"group": "document", "fields": ["content"]}),
    ]
    machine = machine_headers(tenant, "parties:read")
    refused = client.get(content, headers=machine)
    assert (refused.status_code, refused.json()["code"]) == (403, "ROLE_FORBIDDEN")
    other = new_tenant("beta", "admin@beta.mx")
    beta = first_login(
        client, "beta", other.admin_email, other.admin_password, ADMIN_PASSWORD
    )
    assert client.get(content, headers=beta).status_code == 404
    views = [e for e in audit_events(tenant.tenant_id) if e.action == "VIEW_PII"]
    assert len(views) == 1


def test_a_corrupted_object_is_a_500_object_corrupt(
    object_store: LocalFsObjectStore,
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    tmp_path: Path,
):
    _ = object_store
    party_id = _party(client, admin_headers)
    document_id = _upload(client, admin_headers, party_id, "d1").json()["id"]
    stored = (
        tmp_path / "objects" / str(tenant.tenant_id) / f"documents/{party_id}"
    ) / document_id
    _ = stored.write_bytes(b"%PDF-1.4 tampered")
    corrupt = client.get(
        f"{PARTIES}/{party_id}/documents/{document_id}/content",
        headers=admin_headers,
    )
    assert (corrupt.status_code, corrupt.json()["code"]) == (500, "OBJECT_CORRUPT")


def test_legal_hold_is_mlro_only(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    party_id = _party(client, admin_headers)
    path = f"{PARTIES}/{party_id}/legal-hold"
    mlro = mlro_headers(client, tenant)
    held = post(client, mlro, path, {"legal_hold": True}, "h1")
    assert held.status_code == 200, held.text
    assert held.json()["legal_hold"] is True
    again = post(client, mlro, path, {"legal_hold": True}, "h2")
    assert again.status_code == 200, again.text
    assert again.json()["legal_hold"] is True
    admin = post(client, admin_headers, path, {"legal_hold": False}, "h3")
    assert (admin.status_code, admin.json()["code"]) == (403, "ROLE_FORBIDDEN")
    machine = machine_headers(tenant, "parties:write")
    refused = post(client, machine, path, {"legal_hold": False}, "h4")
    assert (refused.status_code, refused.json()["code"]) == (403, "ROLE_FORBIDDEN")
    holds = [
        e.detail
        for e in audit_events(tenant.tenant_id)
        if e.action == "party.legal_hold_set"
    ]
    assert holds == [{"legal_hold": True}]
