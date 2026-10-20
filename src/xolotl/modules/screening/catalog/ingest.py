"""Ingesting a shared list file as a new version (SP-03 spec §6.3).

Shared by the poll job and ``xolotl lists load-file``. A file is parsed in full or
refused; its delta against the active version is checked by the mass-removal guard;
the raw bytes go to the object store under ``SHARED_NAMESPACE``; then the version,
its entry rows and its delta rows are written in the caller's transaction. Nothing
here commits. A failure after the raw file is stored leaves an orphan object that no
row references; the caller logs it.

Entries are stored once per content (spec Decision 3): an unchanged entry keeps its
row, a changed one closes its row (``to_seq``) and opens a new one, a removed one is
closed. The previous version is superseded and the closed rows updated before the
new version and rows are inserted, so the partial unique indexes
``ux_list_version_active`` and ``ux_list_entry_current`` never see two current rows.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from uuid import UUID

from sqlalchemy import Text, insert, literal, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session

from xolotl.modules.screening.catalog.models import (
    ListEntryDelta,
    ListEntryRow,
    ListSource,
    ListVersion,
)
from xolotl.modules.screening.catalog.queries import current_hashes
from xolotl.modules.screening.enums import DeltaChange, VersionStatus
from xolotl.modules.screening.errors import ListMassRemovalError
from xolotl.modules.screening.lists import ListDelta, ListEntry, ParsedList, diff, parse
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.errors import NotFoundError
from xolotl.platform.ids import uuid7
from xolotl.platform.storage import SHARED_NAMESPACE, ObjectStore

_UTF8_BOM = b"\xef\xbb\xbf"
_ZIP_MAGIC = b"PK\x03\x04"


@dataclass(frozen=True, slots=True)
class IngestReport:
    """What one ingestion did; ``version_id`` and ``seq`` are None for a same file."""

    list_code: str
    version_id: UUID | None
    seq: int | None
    entry_count: int
    added: int
    changed: int
    removed: int
    unchanged: int
    parser_version: str
    same_file: bool


def file_kind(data: bytes) -> tuple[str, str]:
    """Sniff a list file's extension and content type from its bytes.

    Returns:
        ``("zip", "application/zip")`` for a ZIP archive, ``("xml",
        "application/xml")`` when the first non-blank character is ``<``, else
        ``("csv", "text/csv")``.

    """
    if data.startswith(_ZIP_MAGIC):
        return "zip", "application/zip"
    if data.removeprefix(_UTF8_BOM).lstrip().startswith(b"<"):
        return "xml", "application/xml"
    return "csv", "text/csv"


def record_success(
    session: Session, source: ListSource, *, now: datetime, changed: bool
) -> None:
    """Mark a fetch or ingestion of the source as a success.

    Clears the error and the failure count and schedules the next poll one interval
    from ``now``; ``last_change_at`` moves only when a new version was written.
    """
    source.last_attempt_at = now
    source.last_success_at = now
    source.last_error = None
    source.consecutive_failures = 0
    source.next_poll_at = now + source.poll_interval
    if changed:
        source.last_change_at = now
    session.flush()


def _enabled_source(session: Session, code: str) -> ListSource:
    source = session.scalar(
        select(ListSource).where(ListSource.code == code, ListSource.enabled)
    )
    if source is None:
        msg = f"no enabled list source {code}"
        raise NotFoundError(msg)
    return source


def _active_version(session: Session, code: str) -> ListVersion | None:
    return session.scalar(
        select(ListVersion).where(
            ListVersion.list_code == code, ListVersion.status == VersionStatus.ACTIVE
        )
    )


def ingest_file(
    session: Session,
    code: str,
    data: bytes,
    *,
    store: ObjectStore,
    loaded_by: str,
    fetched_at: datetime,
    accept_drop: bool = False,
) -> IngestReport:
    """Ingest a published list file as the list's next version.

    A file whose sha256 equals the active version's makes no version: it only
    counts as a success for the source's freshness.

    An unknown or disabled ``code`` raises ``NotFoundError`` and a malformed file
    the library's ``ListFormatError``, both before anything is written.

    Returns:
        The ingestion report.

    """
    source = _enabled_source(session, code)
    active = _active_version(session, code)
    if active is not None and active.file_sha256 == hashlib.sha256(data).digest():
        record_success(session, source, now=clock.now(), changed=False)
        return IngestReport(
            list_code=code,
            version_id=None,
            seq=None,
            entry_count=active.entry_count,
            added=0,
            changed=0,
            removed=0,
            unchanged=active.entry_count,
            parser_version=active.parser_version,
            same_file=True,
        )
    parsed = parse(code, BytesIO(data))
    return ingest_parsed(
        session,
        parsed,
        data=data,
        store=store,
        loaded_by=loaded_by,
        fetched_at=fetched_at,
        accept_drop=accept_drop,
    )


def ingest_parsed(
    session: Session,
    parsed: ParsedList,
    *,
    data: bytes,
    store: ObjectStore,
    loaded_by: str,
    fetched_at: datetime,
    accept_drop: bool = False,
) -> IngestReport:
    """Write an already parsed list as the next version of ``parsed.list_code``.

    ``data`` is what is hashed and stored as the version's raw file. An unknown or
    disabled list code raises ``NotFoundError``; a delta removing more than
    ``lists_max_removal_ratio`` of the active version's entries raises
    ``ListMassRemovalError`` unless ``accept_drop``. Both happen before anything is
    stored or written.

    Returns:
        The ingestion report.

    """
    source = _enabled_source(session, parsed.list_code)
    active = _active_version(session, parsed.list_code)
    code = source.code
    delta = diff(current_hashes(session, code), parsed.entries)
    _guard(active, delta, accept_drop=accept_drop)

    version_id = uuid7()
    seq = 1 if active is None else active.seq + 1
    ext, content_type = file_kind(data)
    ref = store.put(
        SHARED_NAMESPACE,
        f"lists/{code}/{version_id}.{ext}",
        data,
        content_type=content_type,
    )

    # Supersede and close first: the partial unique indexes allow one current row.
    if active is not None:
        _ = session.execute(
            update(ListVersion)
            .where(ListVersion.id == active.id)
            .values(status=VersionStatus.SUPERSEDED)
        )
    previous_ids = _close_entries(session, code, [*delta.changed, *delta.removed], seq)

    _ = session.execute(
        insert(ListVersion).values(
            id=version_id,
            list_code=code,
            seq=seq,
            status=VersionStatus.ACTIVE,
            published_at=parsed.published_at,
            source_version=parsed.source_version,
            parser_version=parsed.parser_version,
            fetched_at=fetched_at,
            loaded_by=loaded_by,
            file_sha256=ref.sha256,
            file_size=ref.size,
            storage_key=ref.key,
            entry_count=len(parsed.entries),
            added=len(delta.added),
            changed=len(delta.changed),
            removed=len(delta.removed),
            previous_version_id=None if active is None else active.id,
        )
    )

    _write_entries(
        session,
        code,
        parsed,
        delta,
        version_id=version_id,
        seq=seq,
        previous_ids=previous_ids,
    )
    record_success(session, source, now=clock.now(), changed=True)
    return IngestReport(
        list_code=code,
        version_id=version_id,
        seq=seq,
        entry_count=len(parsed.entries),
        added=len(delta.added),
        changed=len(delta.changed),
        removed=len(delta.removed),
        unchanged=delta.unchanged,
        parser_version=parsed.parser_version,
        same_file=False,
    )


def _close_entries(
    session: Session, code: str, keys: list[str], seq: int
) -> dict[str, UUID]:
    # The current rows of changed and removed keys leave the list at version seq.
    if not keys:
        return {}
    closed = session.execute(
        update(ListEntryRow)
        .where(
            ListEntryRow.list_code == code,
            ListEntryRow.to_seq.is_(None),
            ListEntryRow.source_entry_key == literal(keys, ARRAY(Text)).any_(),
        )
        .values(to_seq=seq)
        .returning(ListEntryRow.source_entry_key, ListEntryRow.id)
        .execution_options(synchronize_session=False)
    )
    return dict(closed.all())


def _write_entries(
    session: Session,
    code: str,
    parsed: ParsedList,
    delta: ListDelta,
    *,
    version_id: UUID,
    seq: int,
    previous_ids: dict[str, UUID],
) -> None:
    by_key = {entry.entry_key: entry for entry in parsed.entries}
    new_ids = {key: uuid7() for key in (*delta.added, *delta.changed)}
    if new_ids:
        _ = session.execute(
            insert(ListEntryRow),
            [_entry_row(new_ids[key], code, by_key[key], seq) for key in new_ids],
        )
    deltas = [
        *(
            _delta(version_id, key, DeltaChange.ADDED, None, new_ids[key])
            for key in delta.added
        ),
        *(
            _delta(
                version_id, key, DeltaChange.CHANGED, previous_ids[key], new_ids[key]
            )
            for key in delta.changed
        ),
        *(
            _delta(version_id, key, DeltaChange.REMOVED, previous_ids[key], None)
            for key in delta.removed
        ),
    ]
    if deltas:
        _ = session.execute(insert(ListEntryDelta), deltas)


def _guard(active: ListVersion | None, delta: ListDelta, *, accept_drop: bool) -> None:
    if active is None or active.entry_count == 0 or accept_drop:
        return
    removed = len(delta.removed)
    ratio = Decimal(removed) / active.entry_count
    if ratio > get_settings().lists_max_removal_ratio:
        msg = f"removed {removed} of {active.entry_count}"
        raise ListMassRemovalError(msg)


def _entry_row(
    row_id: UUID, code: str, entry: ListEntry, seq: int
) -> dict[str, object]:
    return {
        "id": row_id,
        "list_code": code,
        "source_entry_key": entry.entry_key,
        "content_hash": entry.content_hash(),
        "entity_type": entry.entity_type.value,
        "primary_name": entry.primary_name.full,
        "screenable": entry.screenable,
        "entry": json.loads(entry.canonical_json()),
        "from_seq": seq,
        "to_seq": None,
    }


def _delta(
    version_id: UUID,
    key: str,
    change: DeltaChange,
    prev_entry_id: UUID | None,
    new_entry_id: UUID | None,
) -> dict[str, object]:
    return {
        "version_id": version_id,
        "source_entry_key": key,
        "change": change,
        "prev_entry_id": prev_entry_id,
        "new_entry_id": new_entry_id,
    }
