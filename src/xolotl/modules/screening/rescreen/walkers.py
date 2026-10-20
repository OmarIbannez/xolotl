"""Whole-base re-screens: private-list, periodic and re-index walks (spec §10.3, §10.4).

Three job kinds walk the tenant's ``ACTIVE`` parties in id order,
``SCREENING_RESCREEN_BATCH`` per page (``party_ids_after``), one page per job and
transaction. A page screens each party, advances its batch (``done``, ``runs``,
``new_matches`` and the cursor, the last party id walked) and enqueues the next
page (key ``<kind>:<batch_id>:<last party id>``, payload ``batch_id`` and
``after_party_id``), or completes the batch after a short page. A replayed page
whose cursor the batch has passed is skipped, and a page that runs out of
attempts fails its batch (``page_job``); the next open of the batch resumes it from
its cursor. Purged and merged parties are not walked.

- ``screening.private_list_rescreen``: opened by consumer ``screening`` of
  ``private_list.version_activated.v1`` (batch ``PRIVATE_LIST``, ref the version
  id). Each page builds one ``MemoryIndex`` over the version's screenable entries
  and screens every party against it alone (``mode=PRIVATE_LIST``,
  ``only_index``). The first page suggests ``DELISTED`` on the findings of the
  version's removed and newly non-screenable entries. A version superseded before
  its page runs completes the batch with ``SUPERSEDED_VERSION``: the newer version
  has a batch of its own.
- ``screening.periodic_rescreen``: the monthly schedule's ``{}`` job opens the
  Mexico City month's ``PERIODIC`` batch (ref ``YYYY-MM``) and enqueues its first
  page; a second ``{}`` job that month finds the batch and adds nothing, and once
  it is complete does nothing. Pages screen every party against the full list set
  (``mode=PERIODIC``). The same walk serves a subscription's first version of a
  shared list (ref ``initial:<code>``, opened by ``list_delta``; its pages carry
  ``require_versions``, so the holder screens with at least that version) and
  ``screening rescreen`` (ref ``manual:<UTC timestamp>``).
- ``screening.reindex``: ``ensure_screening_index`` runs as the worker starts and
  opens the ``REINDEX`` batch (ref ``MATCHER_VERSION``) of each tenant whose name
  index holds a row of another matcher version or lacks an ``ACTIVE`` party's
  ``LEGAL`` row. Pages re-index each party's names and screen it (``mode=REINDEX``).

Jobs are system activity: runs carry ``trigger_type = JOB`` and the job id. A walk
that ``screening rescreen`` started runs as its ``cli:<user>``: every page of it
carries ``trigger_type`` and ``trigger_ref`` (the actor id) and its runs carry
``trigger_type = CLI`` and that ref.
"""

from collections.abc import Callable
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from xolotl.modules.party.enums import PartyStatus
from xolotl.modules.party.service import parties_query, party_ids_after
from xolotl.modules.screening.catalog.models import (
    PrivateList,
    PrivateListEntryDelta,
    PrivateListEntryRow,
    PrivateListVersion,
)
from xolotl.modules.screening.catalog.private import private_entry_of
from xolotl.modules.screening.engine.entries import indexed_private_entry
from xolotl.modules.screening.engine.enums import (
    BatchKind,
    NameKind,
    RunMode,
    TriggerType,
)
from xolotl.modules.screening.engine.holder import get_index_holder
from xolotl.modules.screening.engine.models import PartyNameIndex, ScreeningBatch
from xolotl.modules.screening.engine.names import index_party_names
from xolotl.modules.screening.engine.policy import today_mx
from xolotl.modules.screening.engine.screen import RunResult, Trigger, screen
from xolotl.modules.screening.engine.subjects import subject_for_party
from xolotl.modules.screening.enums import DeltaChange, VersionStatus
from xolotl.modules.screening.matching import MATCHER_VERSION, MemoryIndex
from xolotl.modules.screening.rescreen.batches import (
    WALKER_START,
    advance_batch,
    complete_batch,
    lock_batch,
    open_batch,
    page_already_done,
    page_job,
)
from xolotl.modules.screening.rescreen.delisting import suggest_delisting
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import SessionFactory, tenant_session
from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.events import Event, Handler
from xolotl.platform.jobs.queue import Job, JobHandler, PostgresJobQueue
from xolotl.platform.tenancy.context import TenantContext
from xolotl.platform.tenancy.tenants import tenant_ids

PRIVATE_LIST_RESCREEN_KIND = "screening.private_list_rescreen"
PERIODIC_RESCREEN_KIND = "screening.periodic_rescreen"
REINDEX_KIND = "screening.reindex"
REINDEX_ACTOR = "system:screening-reindex"
SUPERSEDED_VERSION = "SUPERSEDED_VERSION"
# The job kind that walks each batch kind a person can start.
_WALK_KINDS = {
    BatchKind.PERIODIC: PERIODIC_RESCREEN_KIND,
    BatchKind.REINDEX: REINDEX_KIND,
}

type ScreenOne = Callable[[UUID], RunResult]


def _page_trigger(job: Job) -> tuple[Trigger, dict[str, JsonValue]]:
    # Who the page's runs are for: the person a ``screening rescreen`` page carries,
    # passed on to the next page; otherwise the job itself.
    raw = job.payload.get("trigger_type")
    if raw is None:
        return Trigger(TriggerType.JOB, str(job.id)), {}
    ref = str(job.payload["trigger_ref"])
    trigger = Trigger(TriggerType(str(raw)), ref)
    return trigger, {"trigger_type": trigger.type.value, "trigger_ref": ref}


def active_party_count(s: Session) -> int:
    """Count the tenant's ``ACTIVE`` parties, a walk's ``total``.

    Returns:
        The count.

    """
    active = parties_query(s, status=PartyStatus.ACTIVE).subquery()
    return s.scalar(select(func.count()).select_from(active)) or 0


def enqueue_walk(
    s: Session,
    queue: PostgresJobQueue,
    *,
    kind: str,
    batch: ScreeningBatch,
    extra: dict[str, JsonValue] | None = None,
) -> UUID | None:
    """Enqueue the walk's page that starts at the batch's cursor.

    A new batch starts at page one; a resumed one after its last committed party.

    Returns:
        The job id, or None when that page is already queued.

    """
    return queue.enqueue(
        s,
        kind=kind,
        key=f"{kind}:{batch.id}:{batch.cursor or WALKER_START}",
        payload={
            "batch_id": str(batch.id),
            **(extra or {}),
            "after_party_id": batch.cursor,
        },
    )


def _page_batch(s: Session, job: Job) -> tuple[ScreeningBatch, UUID | None] | None:
    # The page's batch, locked, and where the page starts; None for a page the
    # batch has already passed.
    batch = lock_batch(s, UUID(str(job.payload["batch_id"])))
    raw = job.payload.get("after_party_id")
    after = None if raw is None else UUID(str(raw))
    if page_already_done(batch, WALKER_START if after is None else str(after)):
        return None
    return batch, after


def _walk_page(
    s: Session,
    queue: PostgresJobQueue,
    batch: ScreeningBatch,
    *,
    kind: str,
    after: UUID | None,
    screen_one: ScreenOne,
    extra: dict[str, JsonValue] | None = None,
) -> None:
    limit = get_settings().screening_rescreen_batch
    party_ids = party_ids_after(s, after=after, limit=limit)
    new_matches = 0
    for party_id in party_ids:
        new_matches += screen_one(party_id).run.new_match_count
    if party_ids:
        advance_batch(
            batch,
            done=len(party_ids),
            runs=len(party_ids),
            new_matches=new_matches,
            cursor=str(party_ids[-1]),
        )
    if len(party_ids) < limit:
        complete_batch(batch)
        return
    _ = enqueue_walk(s, queue, kind=kind, batch=batch, extra=extra)


# ---- private-list versions -------------------------------------------------------


def on_private_list_version_activated(queue: PostgresJobQueue) -> Handler:
    """Build consumer ``screening``'s handler of ``private_list.version_activated.v1``.

    Returns:
        A handler that opens the version's ``PRIVATE_LIST`` batch and enqueues its
        next page.

    """

    def on_version(s: Session, event: Event) -> None:
        version_id = str(event.data["version_id"])
        batch = open_batch(
            s,
            kind=BatchKind.PRIVATE_LIST,
            ref=version_id,
            total=active_party_count(s),
        )
        if batch is None:
            return
        _ = enqueue_walk(
            s,
            queue,
            kind=PRIVATE_LIST_RESCREEN_KIND,
            batch=batch,
            extra={"private_list_version_id": version_id},
        )

    return on_version


def _private_delisted_keys(s: Session, version: PrivateListVersion) -> list[str]:
    # The version's removed and changed keys that the list no longer holds as a
    # screenable entry now. For the active version these are its removed entries
    # and its changed ones that became non-screenable; for a superseded one, a key
    # a later version brought back is left alone.
    keys = set(
        s.scalars(
            select(PrivateListEntryDelta.source_entry_key).where(
                PrivateListEntryDelta.version_id == version.id,
                PrivateListEntryDelta.change.in_((
                    DeltaChange.REMOVED,
                    DeltaChange.CHANGED,
                )),
            )
        )
    )
    if not keys:
        return []
    current = s.scalars(
        select(PrivateListEntryRow).where(
            PrivateListEntryRow.list_id == version.list_id,
            PrivateListEntryRow.to_seq.is_(None),
            PrivateListEntryRow.source_entry_key.in_(keys),
        )
    )
    listed = {
        row.source_entry_key for row in current if private_entry_of(s, row).screenable
    }
    return sorted(keys - listed)


def _version_index(s: Session, private_list: PrivateList) -> MemoryIndex:
    # The active version's screenable entries, blocked with the shared IDF table as
    # the holder's private shards are.
    rows = s.scalars(
        select(PrivateListEntryRow)
        .where(
            PrivateListEntryRow.list_id == private_list.id,
            PrivateListEntryRow.to_seq.is_(None),
        )
        .order_by(PrivateListEntryRow.id)
    )
    indexed = [
        item
        for row in rows
        if (
            item := indexed_private_entry(s, row, private_list.list_code)
        ).entry.screenable
    ]
    return MemoryIndex.build(indexed, idf=get_index_holder().shared(s).blocking_idf)


def handle_private_list_rescreen(queue: PostgresJobQueue) -> JobHandler:
    """Build the ``screening.private_list_rescreen`` page handler.

    Returns:
        The handler, wrapped so the batch fails when the job's last attempt does.

    """

    def run(s: Session, job: Job) -> None:
        page = _page_batch(s, job)
        if page is None:
            return
        batch, after = page
        version_id = UUID(str(job.payload["private_list_version_id"]))
        version = s.scalars(
            select(PrivateListVersion).where(PrivateListVersion.id == version_id)
        ).one()
        private_list = s.scalars(
            select(PrivateList).where(PrivateList.id == version.list_id)
        ).one()
        code = private_list.list_code
        if after is None:
            _ = suggest_delisting(
                s, list_code=code, entry_keys=_private_delisted_keys(s, version)
            )
        if version.status is not VersionStatus.ACTIVE:
            # The newer version has a batch of its own.
            complete_batch(batch, note=SUPERSEDED_VERSION)
            return
        index = _version_index(s, private_list)
        trigger = Trigger(TriggerType.JOB, str(job.id))

        def screen_one(party_id: UUID) -> RunResult:
            return screen(
                s,
                subject_for_party(s, party_id),
                mode=RunMode.PRIVATE_LIST,
                trigger=trigger,
                lists={code},
                only_index=index,
                batch_id=batch.id,
            )

        _walk_page(
            s,
            queue,
            batch,
            kind=PRIVATE_LIST_RESCREEN_KIND,
            after=after,
            screen_one=screen_one,
            extra={"private_list_version_id": str(version_id)},
        )

    return page_job(run)


# ---- periodic and first-version walks --------------------------------------------


def open_periodic(
    s: Session,
    queue: PostgresJobQueue,
    *,
    ref: str,
    require_versions: dict[str, int] | None = None,
) -> None:
    """Open the ``PERIODIC`` batch ``ref`` and enqueue its next page.

    A complete batch is left alone, and a running one whose page is queued gets
    nothing new. ``require_versions`` (a first version's walk) travels with every
    page, so the holder screens with at least those shared versions.
    """
    extra: dict[str, JsonValue] | None = (
        None if require_versions is None else {"require_versions": {**require_versions}}
    )
    _ = _open_walk(s, queue, kind=BatchKind.PERIODIC, ref=ref, extra=extra)


def _required(payload: dict[str, JsonValue]) -> dict[str, int] | None:
    raw = payload.get("require_versions")
    if not isinstance(raw, dict):
        return None
    return {code: int(str(seq)) for code, seq in raw.items()}


def handle_periodic_rescreen(queue: PostgresJobQueue) -> JobHandler:
    """Build the ``screening.periodic_rescreen`` handler.

    The schedule's ``{}`` job opens the month's batch; a job with ``batch_id`` is
    a page of a ``PERIODIC`` batch.

    Returns:
        The handler; pages fail their batch when their last attempt does.

    """

    def walk(s: Session, job: Job) -> None:
        page = _page_batch(s, job)
        if page is None:
            return
        batch, after = page
        trigger, carried = _page_trigger(job)
        required = _required(job.payload)
        extra: dict[str, JsonValue] = {**carried}
        if required is not None:
            extra["require_versions"] = {**required}

        def screen_one(party_id: UUID) -> RunResult:
            return screen(
                s,
                subject_for_party(s, party_id),
                mode=RunMode.PERIODIC,
                trigger=trigger,
                require_versions=required,
                batch_id=batch.id,
            )

        _walk_page(
            s,
            queue,
            batch,
            kind=PERIODIC_RESCREEN_KIND,
            after=after,
            screen_one=screen_one,
            extra=extra,
        )

    page = page_job(walk)

    def run(s: Session, job: Job) -> None:
        if "batch_id" in job.payload:
            page(s, job)
            return
        open_periodic(s, queue, ref=today_mx().strftime("%Y-%m"))

    return run


# ---- re-index ----------------------------------------------------------------------


def handle_reindex(queue: PostgresJobQueue) -> JobHandler:
    """Build the ``screening.reindex`` page handler.

    Returns:
        The handler, wrapped so the batch fails when the job's last attempt does.

    """

    def run(s: Session, job: Job) -> None:
        page = _page_batch(s, job)
        if page is None:
            return
        batch, after = page
        trigger, carried = _page_trigger(job)

        def screen_one(party_id: UUID) -> RunResult:
            _ = index_party_names(s, party_id)
            return screen(
                s,
                subject_for_party(s, party_id),
                mode=RunMode.REINDEX,
                trigger=trigger,
                batch_id=batch.id,
            )

        _walk_page(
            s,
            queue,
            batch,
            kind=REINDEX_KIND,
            after=after,
            screen_one=screen_one,
            extra=carried,
        )

    return page_job(run)


def _index_is_stale(s: Session) -> bool:
    other_version = exists().where(PartyNameIndex.matcher_version != MATCHER_VERSION)
    active = parties_query(s, status=PartyStatus.ACTIVE).subquery()
    unindexed = (
        select(active.c.id)
        .where(
            ~exists().where(
                PartyNameIndex.party_id == active.c.id,
                PartyNameIndex.name_kind == NameKind.LEGAL,
            )
        )
        .exists()
    )
    return bool(s.scalar(select(other_version | unindexed)))


def _open_walk(
    s: Session,
    queue: PostgresJobQueue,
    *,
    kind: BatchKind,
    ref: str,
    extra: dict[str, JsonValue] | None = None,
) -> tuple[ScreeningBatch, UUID | None] | None:
    batch = open_batch(s, kind=kind, ref=ref, total=active_party_count(s))
    if batch is None:
        return None
    job_kind = _WALK_KINDS[kind]
    return batch, enqueue_walk(s, queue, kind=job_kind, batch=batch, extra=extra)


def ensure_screening_index(
    session_factory: SessionFactory, queue: PostgresJobQueue
) -> int:
    """Open a ``REINDEX`` batch for every tenant whose name index is stale.

    Stale means a ``party_name_index`` row of another matcher version, or an
    ``ACTIVE`` party without a ``LEGAL`` row (parties made before the index
    existed). A batch already complete for ``MATCHER_VERSION`` is not reopened, and
    a running one whose page is queued gets nothing new. A worker loop runs it as
    it starts.

    Returns:
        How many tenants had a re-index page enqueued.

    """
    started = 0
    for tenant_id in tenant_ids(session_factory, actor_id=REINDEX_ACTOR):
        with tenant_session(
            TenantContext.system(tenant_id, REINDEX_ACTOR), factory=session_factory
        ) as s:
            if not _index_is_stale(s):
                continue
            opened = _open_walk(s, queue, kind=BatchKind.REINDEX, ref=MATCHER_VERSION)
            if opened is not None and opened[1] is not None:
                started += 1
    return started


def start_rescreen(
    s: Session, queue: PostgresJobQueue, *, kind: BatchKind, trigger: Trigger
) -> UUID | None:
    """Start a full re-screen of the session tenant's parties (``screening rescreen``).

    ``PERIODIC`` opens a batch of its own (ref ``manual:<UTC timestamp>``), apart
    from the month's; ``REINDEX`` opens the batch of ``MATCHER_VERSION``, or
    resumes it. The worker walks it; the page this enqueues, and each page after
    it, carries ``trigger`` (the CLI's ``cli:<user>``), so the walk's runs do too.
    A ``REINDEX`` page already queued keeps the trigger it was queued with.

    Returns:
        The batch id, or None when the ``REINDEX`` batch is already complete.

    Raises:
        ValidationFailedError: For a kind other than ``PERIODIC`` or ``REINDEX``.

    """
    if kind is BatchKind.PERIODIC:
        ref = "manual:" + clock.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    elif kind is BatchKind.REINDEX:
        ref = MATCHER_VERSION
    else:
        msg = "kind must be PERIODIC or REINDEX"
        raise ValidationFailedError(msg)
    extra: dict[str, JsonValue] = {
        "trigger_type": trigger.type.value,
        "trigger_ref": trigger.ref,
    }
    opened = _open_walk(s, queue, kind=kind, ref=ref, extra=extra)
    return None if opened is None else opened[0].id
