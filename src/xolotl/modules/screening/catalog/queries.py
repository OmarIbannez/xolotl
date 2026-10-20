"""Reads of the shared list catalog: active versions, their members and entries.

The members of version ``n`` of a list are its ``list_entry`` rows with
``from_seq <= n`` and ``to_seq`` null or above ``n`` (SP-03 spec §5.1); the rows of
the active version are the ones with ``to_seq`` null.
"""

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from xolotl.modules.screening.catalog.models import ListEntryRow, ListVersion
from xolotl.modules.screening.enums import VersionStatus
from xolotl.modules.screening.lists import ListEntry


def active_versions(session: Session) -> dict[str, ListVersion]:
    """Read the active version of every shared list that has one.

    Returns:
        The active versions by list code.

    """
    rows = session.scalars(
        select(ListVersion).where(ListVersion.status == VersionStatus.ACTIVE)
    )
    return {row.list_code: row for row in rows}


def current_hashes(session: Session, list_code: str) -> dict[str, bytes]:
    """Read the content hash of every entry of the list's active version.

    Returns:
        The content hashes by entry key.

    """
    rows = session.execute(
        select(ListEntryRow.source_entry_key, ListEntryRow.content_hash).where(
            ListEntryRow.list_code == list_code, ListEntryRow.to_seq.is_(None)
        )
    )
    return dict(rows.all())


def version_members(session: Session, list_code: str, seq: int) -> Select[ListEntryRow]:
    """Build the query of the entry rows that make up version ``seq`` of a list.

    ``session`` is unused today; it keeps the signature of the other reads, so a
    caller can switch between them freely.

    Returns:
        The query, for the caller to run, page or stream.

    """
    _ = session
    return (
        select(ListEntryRow)
        .where(
            ListEntryRow.list_code == list_code,
            ListEntryRow.from_seq <= seq,
            or_(ListEntryRow.to_seq.is_(None), ListEntryRow.to_seq > seq),
        )
        .order_by(ListEntryRow.source_entry_key)
    )


def entry_of(row: ListEntryRow) -> ListEntry:
    """Rebuild the library entry a ``list_entry`` row stores.

    Returns:
        The entry, equal to the one that was ingested.

    """
    return ListEntry.from_json(row.entry)
