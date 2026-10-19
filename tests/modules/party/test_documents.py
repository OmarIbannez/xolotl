"""Documents of a party in the object store: upload, refusals, download and digest.

``pytest.raises`` is the outermost context manager wherever a session should roll
back, as in ``test_service.py``.
"""

import hashlib
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, select

from tests.helpers import audit_events, staff_session, system_ctx
from tests.modules.party.conftest import ACME, ANA
from xolotl.modules.party import documents, service
from xolotl.modules.party.enums import DocumentType
from xolotl.modules.party.models import Document
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.audit import VIEW_PII
from xolotl.platform.db import tenant_session
from xolotl.platform.errors import NotFoundError, ValidationFailedError
from xolotl.platform.storage import ObjectIntegrityError, ObjectRef
from xolotl.platform.storage.local import LocalFsObjectStore

PDF = b"%PDF-1.4 hello"


def _upload(
    tenant: CreatedTenant, store: LocalFsObjectStore, data: bytes = PDF
) -> tuple[Document, UUID]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ANA)
        row = documents.upload_document(
            s,
            store,
            party_id=party.id,
            doc_type=DocumentType.IDENTITY,
            content_type="application/pdf",
            data=data,
        )
    return row, party.id


def _documents(tenant: CreatedTenant) -> list[Document]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return list(s.scalars(select(Document)).all())


def test_an_upload_stores_the_bytes_under_the_party_and_records_its_digest(
    engine: Engine,
    tenant: CreatedTenant,
    local_store: LocalFsObjectStore,
    tmp_path: Path,
):
    row, party_id = _upload(tenant, local_store)
    assert row.sha256 == hashlib.sha256(PDF).digest()
    assert row.size == len(PDF)
    assert row.storage_key == f"documents/{party_id}/{row.id}"
    assert row.uploaded_by == str(tenant.admin_user_id)
    stored = tmp_path / "objects" / str(tenant.tenant_id) / row.storage_key
    assert stored.read_bytes() == PDF
    uploaded = audit_events(tenant.tenant_id)[-1]
    assert (uploaded.action, uploaded.entity_type, uploaded.entity_id) == (
        "document.uploaded",
        "document",
        str(row.id),
    )
    assert uploaded.detail == {
        "party_id": str(party_id),
        "doc_type": "IDENTITY",
        "size": len(PDF),
    }


def test_refusals_leave_no_file_and_no_row(
    engine: Engine,
    tenant: CreatedTenant,
    local_store: LocalFsObjectStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ANA)
    monkeypatch.setattr(documents, "MAX_DOCUMENT_BYTES", 16)
    for content_type, data, detail in (
        ("application/pdf", PDF * 2, "document too large"),
        ("text/plain", PDF, "unsupported document content type"),
        ("application/pdf", b"", "empty document"),
    ):
        with (
            pytest.raises(ValidationFailedError) as refused,
            staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
        ):
            _ = documents.upload_document(
                s,
                local_store,
                party_id=party.id,
                doc_type=DocumentType.IDENTITY,
                content_type=content_type,
                data=data,
            )
        assert refused.value.detail == detail
    assert not (tmp_path / "objects" / str(tenant.tenant_id)).exists()
    assert _documents(tenant) == []


def test_a_flush_failure_deletes_the_stored_object(
    engine: Engine,
    tenant: CreatedTenant,
    local_store: LocalFsObjectStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    # The party is created in the same session, so get_party hits the identity map
    # and no autoflush runs the patched flush before the put.
    puts: list[ObjectRef] = []
    real_put = local_store.put

    def recording_put(
        tenant_id: UUID, key: str, data: bytes, *, content_type: str
    ) -> ObjectRef:
        ref = real_put(tenant_id, key, data, content_type=content_type)
        puts.append(ref)
        return ref

    monkeypatch.setattr(local_store, "put", recording_put)
    with (
        pytest.raises(RuntimeError, match="flush refused"),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        party = service.create_party(s, ANA)
        real_flush = s.flush

        def refuse_after_the_put(objects: Sequence[object] | None = None) -> None:
            if puts:
                msg = "flush refused"
                raise RuntimeError(msg)
            real_flush(objects)

        monkeypatch.setattr(s, "flush", refuse_after_the_put)
        _ = documents.upload_document(
            s,
            local_store,
            party_id=party.id,
            doc_type=DocumentType.IDENTITY,
            content_type="application/pdf",
            data=PDF,
        )
    assert len(puts) == 1
    assert not (tmp_path / "objects" / str(tenant.tenant_id) / puts[0].key).exists()
    assert _documents(tenant) == []


def test_the_download_returns_the_bytes_and_records_view_pii_on_the_document(
    engine: Engine, tenant: CreatedTenant, local_store: LocalFsObjectStore
):
    uploaded, party_id = _upload(tenant, local_store)
    assert [e for e in audit_events(tenant.tenant_id) if e.action == VIEW_PII] == []
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        row, data = documents.download_document(
            s, local_store, party_id=uploaded.party_id, document_id=uploaded.id
        )
    assert (row.id, row.party_id, data) == (uploaded.id, party_id, PDF)
    (viewed,) = [e for e in audit_events(tenant.tenant_id) if e.action == VIEW_PII]
    assert (viewed.entity_type, viewed.entity_id) == ("document", str(row.id))
    assert viewed.detail == {"group": "document", "fields": ["content"]}
    assert documents.download_filename(row) == f"{row.id}.pdf"


def test_a_tampered_object_is_refused(
    engine: Engine,
    tenant: CreatedTenant,
    local_store: LocalFsObjectStore,
    tmp_path: Path,
):
    row, _ = _upload(tenant, local_store)
    path = tmp_path / "objects" / str(tenant.tenant_id) / row.storage_key
    _ = path.write_bytes(b"other")
    with (
        pytest.raises(ObjectIntegrityError),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = documents.download_document(
            s, local_store, party_id=row.party_id, document_id=row.id
        )


def test_a_document_of_another_party_is_not_found(
    engine: Engine, tenant: CreatedTenant, local_store: LocalFsObjectStore
):
    row, _ = _upload(tenant, local_store)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        acme = service.create_party(s, ACME)
    with (
        pytest.raises(NotFoundError) as refused,
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = documents.download_document(
            s, local_store, party_id=acme.id, document_id=row.id
        )
    assert refused.value.detail == "document not found"
    assert [e for e in audit_events(tenant.tenant_id) if e.action == VIEW_PII] == []
