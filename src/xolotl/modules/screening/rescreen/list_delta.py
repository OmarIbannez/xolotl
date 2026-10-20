"""Re-screen the parties near a shared list's delta (SP-03 spec §10.2, FR-SC-003).

Consumer ``screening`` receives ``lists.version_published.v1`` for each version a
tenant's subscription is handed (3a's catch-up). A subscription's first version
(``initial`` true) is no delta: it opens the ``PERIODIC`` batch ``initial:<code>``,
which walks every party against the full list set (``walkers``). For a delta it
opens the tenant's ``LIST_DELTA`` batch of the version (ref the version id, total
``added + changed + removed``) and enqueues its first page, all in the dispatcher's
transaction, so a redelivered event finds both already there. A batch that failed
earlier is reopened and its next page starts at the batch's cursor.

Each ``screening.list_delta`` job (payload ``batch_id``, ``version_id``, ``offset``)
handles one page in one transaction:

1. On the first page only, the delisted entries: every ``REMOVED`` entry and every
   ``CHANGED`` one whose new row is non-screenable (69-B ``DESVIRTUADO`` or
   ``SENTENCIA_FAVORABLE``) is not screened; the tenant's findings on it that still
   await or hold a decision get ``suggested_disposition = DELISTED`` and one
   ``screening.match_delisting_suggested.v1`` each (``suggest_delisting``).
2. The page: ``SCREENING_DELTA_BATCH`` ``ADDED`` and ``CHANGED`` delta rows whose new
   row is screenable, in entry-key order from ``offset``.
3. Blocking in Postgres (``reverse_candidates``) under the tenant's session, with
   the blocking IDF of a shared shard that holds at least this version.
4. Each candidate ``ACTIVE`` party is screened with ``screen(mode=LIST_DELTA,
   only_entries=<the page's entries it was blocked on>)``, so the run is
   ``restricted_to_delta`` and an unchanged entry is never scored. Purged and merged
   parties are skipped.
5. The batch advances by the page (``done`` counts the screened delta entries), and
   either the next page is enqueued (key ``screening.list_delta:<batch>:<offset>``)
   or, after a short page, the batch completes.

Overflow: an entry with more than ``BLOCKING_CAP`` candidates keeps only its RFC,
CURP and passport matches in the delta; its name candidates are left to the next
periodic run. The entry is logged at WARNING (``screening.delta_entry_overflow``,
list code and entry key only), the count travels with the page payloads
(``overflow``), and the completed batch records it as ``OVERFLOW:<n>`` in
``last_error`` while staying ``COMPLETED``.

Resuming a failed batch: when a page's last attempt fails, the batch is marked
``FAILED`` and keeps its cursor. The operator path to resume it is to replay the
``lists.version_published.v1`` event for consumer ``screening`` (the bus replay);
the next open of the same ``(kind, ref)`` reopens the batch and starts at its
cursor. F4's ``screening rescreen`` CLI does not reopen ``LIST_DELTA`` batches, as
it takes ``PERIODIC`` and ``REINDEX`` only. Pages left unscreened otherwise fall to
the next periodic run.
"""

import logging
from collections import defaultdict
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.party.enums import PartyStatus
from xolotl.modules.party.service import get_party
from xolotl.modules.screening.catalog.models import (
    ListEntryDelta,
    ListEntryRow,
    ListVersion,
)
from xolotl.modules.screening.engine.entries import indexed_shared_entry
from xolotl.modules.screening.engine.enums import BatchKind, RunMode, TriggerType
from xolotl.modules.screening.engine.holder import get_index_holder
from xolotl.modules.screening.engine.screen import Trigger, screen
from xolotl.modules.screening.engine.subjects import subject_for_party
from xolotl.modules.screening.enums import DeltaChange
from xolotl.modules.screening.matching import IndexedEntry
from xolotl.modules.screening.rescreen.batches import (
    advance_batch,
    complete_batch,
    lock_batch,
    open_batch,
    page_already_done,
    page_job,
)
from xolotl.modules.screening.rescreen.blocking import reverse_candidates
from xolotl.modules.screening.rescreen.delisting import suggest_delisting
from xolotl.modules.screening.rescreen.walkers import open_periodic
from xolotl.platform.config import get_settings
from xolotl.platform.events import Event, Handler
from xolotl.platform.jobs.queue import Job, JobHandler, PostgresJobQueue

logger = logging.getLogger(__name__)

LIST_DELTA_KIND = "screening.list_delta"


def _int(value: JsonValue) -> int:
    return int(str(value))


def _enqueue_page(
    s: Session,
    queue: PostgresJobQueue,
    *,
    batch_id: UUID,
    version_id: str,
    offset: int,
    overflow: int,
) -> None:
    _ = queue.enqueue(
        s,
        kind=LIST_DELTA_KIND,
        key=f"{LIST_DELTA_KIND}:{batch_id}:{offset}",
        payload={
            "batch_id": str(batch_id),
            "version_id": version_id,
            "offset": offset,
            "overflow": overflow,
        },
    )


def on_list_version_published(queue: PostgresJobQueue) -> Handler:
    """Build consumer ``screening``'s handler of ``lists.version_published.v1``.

    Returns:
        A handler that opens the version's ``LIST_DELTA`` batch and enqueues its
        next page: page one for a new batch, the cursor's page for a resumed one.

    """

    def on_version(s: Session, event: Event) -> None:
        data = event.data
        if data.get("initial") is True:
            # A subscription's first version is a full walk, not a delta.
            code = str(data["list_code"])
            open_periodic(
                s,
                queue,
                ref=f"initial:{code}",
                require_versions={code: _int(data["seq"])},
            )
            return
        total = sum(_int(data[field]) for field in ("added", "changed", "removed"))
        version_id = str(data["version_id"])
        batch = open_batch(s, kind=BatchKind.LIST_DELTA, ref=version_id, total=total)
        if batch is None:
            return
        _enqueue_page(
            s,
            queue,
            batch_id=batch.id,
            version_id=version_id,
            offset=int(batch.cursor or 0),
            overflow=0,
        )

    return on_version


def _delisted_keys(s: Session, version_id: UUID) -> list[str]:
    removed = select(ListEntryDelta.source_entry_key).where(
        ListEntryDelta.version_id == version_id,
        ListEntryDelta.change == DeltaChange.REMOVED,
    )
    unscreenable = (
        select(ListEntryDelta.source_entry_key)
        .join(ListEntryRow, ListEntryRow.id == ListEntryDelta.new_entry_id)
        .where(
            ListEntryDelta.version_id == version_id,
            ListEntryDelta.change == DeltaChange.CHANGED,
            ListEntryRow.screenable.is_(False),
        )
    )
    return sorted({*s.scalars(removed), *s.scalars(unscreenable)})


def _page(
    s: Session, version_id: UUID, *, offset: int, limit: int
) -> list[ListEntryRow]:
    query = (
        select(ListEntryRow)
        .join(ListEntryDelta, ListEntryDelta.new_entry_id == ListEntryRow.id)
        .where(
            ListEntryDelta.version_id == version_id,
            ListEntryDelta.change.in_((DeltaChange.ADDED, DeltaChange.CHANGED)),
            ListEntryRow.screenable,
        )
        .order_by(ListEntryDelta.source_entry_key)
        .offset(offset)
        .limit(limit)
    )
    return list(s.scalars(query))


def _blocked_by_party(
    s: Session, rows: list[ListEntryRow], version: ListVersion
) -> tuple[dict[UUID, list[IndexedEntry]], int]:
    # The parties near each entry, inverted: each party with the page's entries it
    # was blocked on, in entry-key order.
    shard = get_index_holder().shared(s, require={version.list_code: version.seq})
    indexed = [indexed_shared_entry(row) for row in rows]
    blocked = reverse_candidates(s, [i.entry for i in indexed], idf=shard.blocking_idf)
    by_party: dict[UUID, list[IndexedEntry]] = defaultdict(list)
    overflow = 0
    for entry, found in zip(indexed, blocked, strict=True):
        if found.overflow:
            overflow += 1
            logger.warning(
                "screening.delta_entry_overflow",
                extra={"list_code": version.list_code, "entry_key": found.entry_key},
            )
        for party_id in found.party_ids:
            by_party[party_id].append(entry)
    return by_party, overflow


def handle_list_delta(queue: PostgresJobQueue) -> JobHandler:
    """Build the ``screening.list_delta`` page handler.

    Returns:
        The handler, wrapped so the batch fails when the job's last attempt does.

    """

    def run(s: Session, job: Job) -> None:
        payload = job.payload
        offset = _int(payload["offset"])
        batch = lock_batch(s, UUID(str(payload["batch_id"])))
        if page_already_done(batch, str(offset)):
            return
        version_id = UUID(str(payload["version_id"]))
        version = s.get_one(ListVersion, version_id)
        if offset == 0:
            _ = suggest_delisting(
                s,
                list_code=version.list_code,
                entry_keys=_delisted_keys(s, version_id),
            )
        limit = get_settings().screening_delta_batch
        rows = _page(s, version_id, offset=offset, limit=limit)
        by_party, overflow = _blocked_by_party(s, rows, version)
        overflow += _int(payload.get("overflow", 0))
        runs = new_matches = 0
        for party_id in sorted(by_party):
            if get_party(s, party_id).status is not PartyStatus.ACTIVE:
                continue
            result = screen(
                s,
                subject_for_party(s, party_id),
                mode=RunMode.LIST_DELTA,
                trigger=Trigger(TriggerType.JOB, str(job.id)),
                only_entries=by_party[party_id],
                require_versions={version.list_code: version.seq},
                batch_id=batch.id,
            )
            runs += 1
            new_matches += result.run.new_match_count
        following = offset + len(rows)
        advance_batch(
            batch,
            done=len(rows),
            runs=runs,
            new_matches=new_matches,
            cursor=str(following),
        )
        if len(rows) < limit:
            complete_batch(batch, note=f"OVERFLOW:{overflow}" if overflow else None)
            return
        _enqueue_page(
            s,
            queue,
            batch_id=batch.id,
            version_id=str(version_id),
            offset=following,
            overflow=overflow,
        )

    return page_job(run)
