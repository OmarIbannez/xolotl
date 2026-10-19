"""A party's documents: bytes in the object store, metadata in ``document``.

The object is stored before its row, under ``documents/<party_id>/<document_id>``,
and deleted again (best effort) when the row cannot be flushed. The row keeps the
object's sha256 and size, so a download refuses bytes that changed. Downloading
shows a person the document, so it records ``VIEW_PII`` before reading it.
"""

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy.orm import Session

from xolotl.modules.party.enums import DocumentType
from xolotl.modules.party.models import Document
from xolotl.modules.party.service import get_party
from xolotl.platform.audit import VIEW_PII, AuditEvent, record
from xolotl.platform.db import bound_context
from xolotl.platform.errors import NotFoundError, ValidationFailedError
from xolotl.platform.ids import uuid7
from xolotl.platform.storage import ObjectRef, ObjectStore

MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset({
    "application/pdf",
    "image/jpeg",
    "image/png",
})
EXTENSIONS: Mapping[str, str] = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
}


def storage_key(party_id: UUID, document_id: UUID) -> str:
    """Name a document's object.

    Returns:
        ``documents/<party_id>/<document_id>``.

    """
    return f"documents/{party_id}/{document_id}"


def upload_document(
    s: Session,
    store: ObjectStore,
    *,
    party_id: UUID,
    doc_type: DocumentType,
    content_type: str,
    data: bytes,
) -> Document:
    """Store a document of a party and record its digest.

    Returns:
        The new document row, flushed.

    Raises:
        ValidationFailedError: When the document is empty, larger than
            ``MAX_DOCUMENT_BYTES`` or of a content type outside
            ``ALLOWED_CONTENT_TYPES``.

    """
    party = get_party(s, party_id)
    if not data:
        msg = "empty document"
        raise ValidationFailedError(msg)
    if len(data) > MAX_DOCUMENT_BYTES:
        msg = "document too large"
        raise ValidationFailedError(msg)
    if content_type not in ALLOWED_CONTENT_TYPES:
        msg = "unsupported document content type"
        raise ValidationFailedError(msg)
    document_id = uuid7()
    ref = store.put(
        party.tenant_id,
        storage_key(party.id, document_id),
        data,
        content_type=content_type,
    )
    row = Document(
        tenant_id=party.tenant_id,
        id=document_id,
        party_id=party.id,
        doc_type=doc_type,
        storage_key=ref.key,
        sha256=ref.sha256,
        size=ref.size,
        content_type=content_type,
        uploaded_by=bound_context(s).actor_id,
    )
    s.add(row)
    try:
        s.flush()
    except Exception:
        store.delete(ref)
        raise
    _ = record(
        s,
        AuditEvent(
            action="document.uploaded",
            entity_type="document",
            entity_id=str(row.id),
            detail={
                "party_id": str(party.id),
                "doc_type": doc_type.value,
                "size": ref.size,
            },
        ),
    )
    return row


def document_ref(row: Document) -> ObjectRef:
    """Point at a document's object with the digest its row recorded.

    Returns:
        The ref the object store checks the bytes against.

    """
    return ObjectRef(
        tenant_id=row.tenant_id,
        key=row.storage_key,
        sha256=row.sha256,
        size=row.size,
        content_type=row.content_type,
    )


def download_document(
    s: Session, store: ObjectStore, *, party_id: UUID, document_id: UUID
) -> tuple[Document, bytes]:
    """Read a party's document for a person, recording ``VIEW_PII`` first.

    Bytes that no longer match the recorded digest raise ``ObjectIntegrityError``
    (500 ``OBJECT_CORRUPT``).

    Returns:
        The row and the document's bytes.

    Raises:
        NotFoundError: When the party has no such document.

    """
    party = get_party(s, party_id)
    row = s.get(Document, (party.tenant_id, document_id))
    if row is None or row.party_id != party.id:
        msg = "document not found"
        raise NotFoundError(msg)
    _ = record(
        s,
        AuditEvent(
            action=VIEW_PII,
            entity_type="document",
            entity_id=str(row.id),
            detail={"group": "document", "fields": ["content"]},
        ),
    )
    return row, store.get(document_ref(row))


def download_filename(row: Document) -> str:
    """Name a download after the document's id and content type.

    Returns:
        ``<document_id>.<pdf|jpg|png>``.

    """
    return f"{row.id}.{EXTENSIONS[row.content_type]}"
