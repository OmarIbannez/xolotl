"""A tenant's private lists: ``MX_LPB`` and ``INTERNAL_<SLUG>`` (SP-03 spec §7).

An upload is read and validated whole (``catalog.template``), then written as the
list's next version in the caller's transaction, the way shared versions are
(``catalog.ingest``): the previous version is superseded and the changed and removed
entries closed (``to_seq``) before the new version and rows are inserted, so the
partial unique indexes never see two current rows. There is no mass-removal guard: a
confidential list may legitimately shrink.

Nothing of an upload is stored in the clear. Entries are ``EncryptedJson`` under the
tenant DATA key and are compared through an HMAC ``content_tag`` under the tenant
INDEX key (plan Deviation 6), so equal names do not give equal bytes across tenants
or to a dictionary attack; the uploaded file is sealed with the DATA key before it
reaches the object store. Each version is audited with counts only and handed to
re-screening through the staff-only ``private_list.version_activated.v1``.
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import cast
from uuid import UUID

from psycopg.errors import UniqueViolation
from sqlalchemy import Text, and_, insert, literal, or_, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from xolotl.modules.screening.catalog.models import (
    PrivateList,
    PrivateListEntryDelta,
    PrivateListEntryRow,
    PrivateListVersion,
)
from xolotl.modules.screening.catalog.template import (
    CSV_TYPE,
    XLSX_TYPE,
    read_template,
    rows_to_entries,
)
from xolotl.modules.screening.enums import DeltaChange, ListKind, VersionStatus
from xolotl.modules.screening.errors import PrivateListExistsError
from xolotl.modules.screening.events import (
    PRIVATE_LIST_VERSION_ACTIVATED,
    publish_screening_event,
)
from xolotl.modules.screening.lists import JsonValue, ListDelta, ListEntry
from xolotl.platform.api.pagination import PageRequest
from xolotl.platform.audit import VIEW_PII, AuditEvent, record
from xolotl.platform.config import get_settings
from xolotl.platform.crypto import (
    aad_for,
    encrypt_json,
    encrypt_value,
    get_key_provider,
)
from xolotl.platform.db import bound_context
from xolotl.platform.enums import KeyPurpose
from xolotl.platform.errors import NotFoundError, ValidationFailedError
from xolotl.platform.ids import uuid7
from xolotl.platform.storage import ObjectStore

LPB_CODE = "MX_LPB"
SEALED_CONTENT_TYPE = "application/octet-stream"
_INTERNAL_CODE = re.compile(r"^INTERNAL_[A-Z0-9_]{1,40}$", re.ASCII)
_INTERNAL_KINDS = frozenset({ListKind.SANCTION, ListKind.PEP, ListKind.INTERNAL})
_KEY_TAG_PREFIX = b"private_list_entry_key:"
_CONTENT_TAG_PREFIX = b"private_list_entry_content:"
_KEY_TAG_LEN = 32
_MIB = 1024 * 1024


def create_private_list(
    s: Session, *, list_code: str, title: str, list_kind: ListKind | None
) -> PrivateList:
    """Create one of the tenant's private lists.

    ``MX_LPB`` is always ``BLOCKED``; an ``INTERNAL_<SLUG>`` list is ``SANCTION``,
    ``PEP`` or ``INTERNAL`` (the default). The route checks who may do this.

    Returns:
        The new list, flushed.

    Raises:
        ValidationFailedError: When the code, the kind or the title is not allowed.
        PrivateListExistsError: When the tenant already has a list with the code.
        IntegrityError: When another constraint refuses the list.

    """
    if list_code == LPB_CODE:
        if list_kind not in {None, ListKind.BLOCKED}:
            msg = "MX_LPB is a BLOCKED list"
            raise ValidationFailedError(msg)
        kind = ListKind.BLOCKED
    elif _INTERNAL_CODE.fullmatch(list_code) is not None:
        kind = list_kind or ListKind.INTERNAL
        if kind not in _INTERNAL_KINDS:
            msg = "an internal list is SANCTION, PEP or INTERNAL"
            raise ValidationFailedError(msg)
    else:
        msg = "the list code must be MX_LPB or INTERNAL_<SLUG>"
        raise ValidationFailedError(msg)
    if not title.strip():
        msg = "the title is required"
        raise ValidationFailedError(msg)
    ctx = bound_context(s)
    private_list = PrivateList(
        tenant_id=ctx.require_tenant_id(),
        id=uuid7(),
        list_code=list_code,
        list_kind=kind,
        title=title.strip(),
        created_by=ctx.actor_id,
    )
    # The unique (tenant_id, list_code) decides between concurrent creations; the
    # savepoint keeps the caller's transaction usable when it refuses this one. The
    # caller's pending changes are flushed first, so the savepoint's flush holds
    # this list alone and its unique violation can only be the list code's.
    s.flush()
    try:
        with s.begin_nested():
            s.add(private_list)
    except IntegrityError as exc:
        if isinstance(exc.orig, UniqueViolation):
            raise PrivateListExistsError from None
        raise
    _ = record(
        s,
        AuditEvent(
            action="private_list.created",
            entity_type="private_list",
            entity_id=str(private_list.id),
            detail={"list_code": list_code, "list_kind": kind.value},
        ),
    )
    return private_list


def get_private_list(s: Session, list_id: UUID) -> PrivateList:
    """Read one of the session tenant's private lists.

    Returns:
        The list.

    Raises:
        NotFoundError: When the tenant has no list ``list_id`` (another tenant's is
            invisible under row-level security).

    """
    private_list = s.get(PrivateList, (bound_context(s).require_tenant_id(), list_id))
    if private_list is None:
        msg = "private list not found"
        raise NotFoundError(msg)
    return private_list


def _key_tagger(index_key: bytes) -> Callable[[str], str]:
    def key_tag(text: str) -> str:
        digest = hmac.new(
            index_key, _KEY_TAG_PREFIX + text.encode(), hashlib.sha256
        ).hexdigest()
        return digest[:_KEY_TAG_LEN]

    return key_tag


def content_tag(index_key: bytes, entry: ListEntry) -> bytes:
    """Tag an entry's content under the tenant INDEX key.

    Returns:
        The HMAC-SHA256 of the entry's canonical JSON.

    """
    return hmac.new(
        index_key, _CONTENT_TAG_PREFIX + entry.canonical_json(), hashlib.sha256
    ).digest()


def upload_private_version(
    s: Session, store: ObjectStore, *, list_id: UUID, data: bytes, content_type: str
) -> PrivateListVersion:
    """Validate an uploaded template file and write it as the list's next version.

    ``content_type`` is ``CSV_TYPE`` or ``XLSX_TYPE`` (parameters such as a charset
    are ignored). Nothing is stored or written unless every row is valid.

    Returns:
        The new active version.

    Raises:
        NotFoundError: When the tenant has no list ``list_id``.
        ValidationFailedError: When the file is empty, larger than
            ``private_list_max_mb`` MiB or of another content type.

    """
    # The lock serialises uploads to one list, so each reads the version it follows.
    private_list = s.scalar(
        select(PrivateList).where(PrivateList.id == list_id).with_for_update()
    )
    if private_list is None:
        msg = "private list not found"
        raise NotFoundError(msg)
    settings = get_settings()
    if not data:
        msg = "empty file"
        raise ValidationFailedError(msg)
    if len(data) > settings.private_list_max_mb * _MIB:
        msg = "file too large"
        raise ValidationFailedError(msg)
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type not in {CSV_TYPE, XLSX_TYPE}:
        msg = "unsupported content type"
        raise ValidationFailedError(msg)
    rows = read_template(data, media_type, max_rows=settings.private_list_max_rows)
    index_key = get_key_provider().index_key(private_list.tenant_id, session=s)
    entries = rows_to_entries(
        rows, list_code=private_list.list_code, key_tag=_key_tagger(index_key)
    )
    return write_private_version(
        s,
        store,
        private_list=private_list,
        entries=entries,
        data=data,
        content_type=media_type,
    )


def private_delta(
    previous: Mapping[str, bytes], current: Mapping[str, bytes]
) -> ListDelta:
    """Compare two versions of a private list by their entries' content tags.

    Returns:
        The added, changed and removed keys, each sorted, and the unchanged count.

    """
    added = sorted(key for key in current if key not in previous)
    changed = sorted(
        key for key, tag in current.items() if key in previous and previous[key] != tag
    )
    removed = sorted(key for key in previous if key not in current)
    return ListDelta(
        added=tuple(added),
        changed=tuple(changed),
        removed=tuple(removed),
        unchanged=len(current) - len(added) - len(changed),
    )


def _active_version(s: Session, list_id: UUID) -> PrivateListVersion | None:
    return s.scalar(
        select(PrivateListVersion).where(
            PrivateListVersion.list_id == list_id,
            PrivateListVersion.status == VersionStatus.ACTIVE,
        )
    )


def _current_tags(s: Session, list_id: UUID) -> dict[str, bytes]:
    return dict(
        s.execute(
            select(
                PrivateListEntryRow.source_entry_key, PrivateListEntryRow.content_tag
            ).where(
                PrivateListEntryRow.list_id == list_id,
                PrivateListEntryRow.to_seq.is_(None),
            )
        ).all()
    )


def write_private_version(
    s: Session,
    store: ObjectStore,
    *,
    private_list: PrivateList,
    entries: Sequence[ListEntry],
    data: bytes,
    content_type: str,
) -> PrivateListVersion:
    """Write validated entries as the next version of a private list.

    ``data`` is the upload: its sha256 and size are recorded, and it is stored sealed
    under the tenant DATA key. An upload identical to the active version still makes
    a new version, as an explicit act of the compliance officer.

    Returns:
        The new active version.

    Raises:
        ValueError: When two entries share an entry key.

    """
    tenant_id = private_list.tenant_id
    list_id = private_list.id
    index_key = get_key_provider().index_key(tenant_id, session=s)
    tags = {entry.entry_key: content_tag(index_key, entry) for entry in entries}
    if len(tags) != len(entries):
        msg = "two entries share an entry key"
        raise ValueError(msg)
    active = _active_version(s, list_id)
    delta = private_delta(_current_tags(s, list_id), tags)
    version_id = uuid7()
    seq = 1 if active is None else active.seq + 1

    key_version, dek = get_key_provider().data_key(
        tenant_id, KeyPurpose.DATA, session=s
    )
    sealed = encrypt_value(
        dek,
        version=key_version,
        plaintext=data,
        aad=aad_for(tenant_id, KeyPurpose.DATA),
    )
    del dek
    ref = store.put(
        tenant_id,
        f"private-lists/{list_id}/{version_id}",
        sealed,
        content_type=SEALED_CONTENT_TYPE,
    )

    # Supersede and close first: the partial unique indexes allow one current row.
    if active is not None:
        _ = s.execute(
            update(PrivateListVersion)
            .where(PrivateListVersion.id == active.id)
            .values(status=VersionStatus.SUPERSEDED)
        )
    previous_ids = _close_entries(s, list_id, [*delta.changed, *delta.removed], seq)
    version = PrivateListVersion(
        tenant_id=tenant_id,
        id=version_id,
        list_id=list_id,
        seq=seq,
        status=VersionStatus.ACTIVE,
        uploaded_by=bound_context(s).actor_id,
        file_sha256=hashlib.sha256(data).digest(),
        file_size=len(data),
        content_type=content_type,
        storage_key=ref.key,
        entry_count=len(entries),
        added=len(delta.added),
        changed=len(delta.changed),
        removed=len(delta.removed),
    )
    s.add(version)
    s.flush()
    _write_entries(
        s,
        private_list,
        entries,
        tags,
        delta,
        version_id=version_id,
        seq=seq,
        previous_ids=previous_ids,
    )
    _ = publish_screening_event(
        s,
        PRIVATE_LIST_VERSION_ACTIVATED,
        entity="private_list_version",
        entity_id=version_id,
        key=str(list_id),
        data={"private_list_id": str(list_id), "version_id": str(version_id)},
    )
    _ = record(
        s,
        AuditEvent(
            action="private_list.version_uploaded",
            entity_type="private_list_version",
            entity_id=str(version_id),
            detail={
                "list_id": str(list_id),
                "version_id": str(version_id),
                "seq": seq,
                "entry_count": len(entries),
                "added": len(delta.added),
                "changed": len(delta.changed),
                "removed": len(delta.removed),
            },
        ),
    )
    return version


def _close_entries(
    s: Session, list_id: UUID, keys: list[str], seq: int
) -> dict[str, UUID]:
    # The current rows of changed and removed keys leave the list at version seq.
    if not keys:
        return {}
    closed = s.execute(
        update(PrivateListEntryRow)
        .where(
            PrivateListEntryRow.list_id == list_id,
            PrivateListEntryRow.to_seq.is_(None),
            PrivateListEntryRow.source_entry_key == literal(keys, ARRAY(Text)).any_(),
        )
        .values(to_seq=seq)
        .returning(PrivateListEntryRow.source_entry_key, PrivateListEntryRow.id)
        .execution_options(synchronize_session=False)
    )
    return dict(closed.all())


def _write_entries(
    s: Session,
    private_list: PrivateList,
    entries: Sequence[ListEntry],
    tags: Mapping[str, bytes],
    delta: ListDelta,
    *,
    version_id: UUID,
    seq: int,
    previous_ids: Mapping[str, UUID],
) -> None:
    tenant_id = private_list.tenant_id
    by_key = {entry.entry_key: entry for entry in entries}
    new_ids = {key: uuid7() for key in (*delta.added, *delta.changed)}
    if new_ids:
        _ = s.execute(
            insert(PrivateListEntryRow),
            [
                {
                    "tenant_id": tenant_id,
                    "id": new_ids[key],
                    "list_id": private_list.id,
                    "source_entry_key": key,
                    "content_tag": tags[key],
                    "entity_type": by_key[key].entity_type.value,
                    "entry": encrypt_json(
                        cast(
                            "dict[str, object]",
                            json.loads(by_key[key].canonical_json()),
                        ),
                        tenant_id=tenant_id,
                    ),
                    "from_seq": seq,
                    "to_seq": None,
                }
                for key in new_ids
            ],
        )
    rows = [
        *((key, DeltaChange.ADDED, None, new_ids[key]) for key in delta.added),
        *(
            (key, DeltaChange.CHANGED, previous_ids[key], new_ids[key])
            for key in delta.changed
        ),
        *((key, DeltaChange.REMOVED, previous_ids[key], None) for key in delta.removed),
    ]
    if rows:
        _ = s.execute(
            insert(PrivateListEntryDelta),
            [
                {
                    "tenant_id": tenant_id,
                    "version_id": version_id,
                    "source_entry_key": key,
                    "change": change,
                    "prev_entry_id": prev_id,
                    "new_entry_id": new_id,
                }
                for key, change, prev_id, new_id in rows
            ],
        )


def active_private_versions(s: Session) -> list[tuple[PrivateList, PrivateListVersion]]:
    """List the session tenant's private lists that have an active version.

    Returns:
        Each list with its active version, ordered by list code.

    """
    rows = s.execute(
        select(PrivateList, PrivateListVersion)
        .join(
            PrivateListVersion,
            (PrivateListVersion.tenant_id == PrivateList.tenant_id)
            & (PrivateListVersion.list_id == PrivateList.id),
        )
        .where(PrivateListVersion.status == VersionStatus.ACTIVE)
        .order_by(PrivateList.list_code)
    ).all()
    return [(private_list, version) for private_list, version in rows]


def private_entry_of(s: Session, row: PrivateListEntryRow) -> ListEntry:
    """Decrypt a private entry row for processing (the engine is not a person).

    No ``VIEW_PII`` is recorded: showing an entry to a person goes through the
    entries view, which audits it. The key is read in ``s``'s transaction.

    Returns:
        The entry as uploaded.

    """
    data = row.entry.reveal(session=s)
    return ListEntry.from_json(cast("dict[str, JsonValue]", data))


def _encode_entry_cursor(from_seq: int, id_: UUID) -> str:
    raw = f"{from_seq}|{id_}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_entry_cursor(cursor: str) -> tuple[int, UUID]:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded).decode()
        # Without a separator the id is empty, which UUID refuses.
        seq, _, ident = raw.partition("|")
        return int(seq), UUID(ident)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        msg = "invalid cursor"
        raise ValidationFailedError(msg) from None


def view_entries(
    s: Session, list_id: UUID, *, page: PageRequest
) -> tuple[list[tuple[PrivateListEntryRow, ListEntry]], str | None]:
    """Show a person one page of the members of a private list's active version.

    The page is ordered by ``(from_seq, id)``. When it holds any entry, one
    ``VIEW_PII`` event (field ``entries``) and one ``private_list.entries_viewed``
    with the count are recorded before any value is decrypted. A cursor this
    function did not issue is a ``ValidationFailedError``.

    Returns:
        The page's rows with their decrypted entries, and the cursor of the next
        page (``None`` on the last one).

    """
    private_list = get_private_list(s, list_id)
    stmt = select(PrivateListEntryRow).where(
        PrivateListEntryRow.list_id == list_id, PrivateListEntryRow.to_seq.is_(None)
    )
    if page.cursor is not None:
        after_seq, after_id = _decode_entry_cursor(page.cursor)
        stmt = stmt.where(
            or_(
                PrivateListEntryRow.from_seq > after_seq,
                and_(
                    PrivateListEntryRow.from_seq == after_seq,
                    PrivateListEntryRow.id > after_id,
                ),
            )
        )
    rows = list(
        s.scalars(
            stmt.order_by(PrivateListEntryRow.from_seq, PrivateListEntryRow.id).limit(
                page.limit + 1
            )
        )
    )
    next_cursor = None
    if len(rows) > page.limit:
        rows = rows[: page.limit]
        next_cursor = _encode_entry_cursor(rows[-1].from_seq, rows[-1].id)
    if not rows:
        return [], None
    # Audited first: if either record fails, nothing is decrypted.
    _ = record(
        s,
        AuditEvent(
            action=VIEW_PII,
            entity_type="private_list",
            entity_id=str(private_list.id),
            detail={"field": "entries"},
        ),
    )
    _ = record(
        s,
        AuditEvent(
            action="private_list.entries_viewed",
            entity_type="private_list",
            entity_id=str(private_list.id),
            detail={"list_id": str(private_list.id), "count": len(rows)},
        ),
    )
    return [(row, private_entry_of(s, row)) for row in rows], next_cursor
