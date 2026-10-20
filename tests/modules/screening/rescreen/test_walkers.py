"""Whole-base re-screens: private-list, periodic and re-index walks (spec §10.3, §10.4).

Each walk screens every ``ACTIVE`` party of its tenant once per batch, page by page
in id order. A private-list version screens the base against that version alone and
suggests ``DELISTED`` for its removed entries; the monthly schedule and a
subscription's first version walk the full list set; a stale name index is rebuilt
and re-screened when the worker starts.
"""

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Unpack
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, func, select, text, update
from sqlalchemy.orm import Session

from tests.helpers import new_tenant, staff_session, system_ctx
from tests.modules.screening.catalog.support import (
    load_private,
    load_shared,
    seed_sources,
)
from tests.modules.screening.review.support import assign_itf
from tests.platform.jobs_support import enqueue
from xolotl.app import build_worker
from xolotl.modules.party import service
from xolotl.modules.party.enums import PartyType
from xolotl.modules.party.retention import purge_party
from xolotl.modules.party.schemas import PartyCreateRequest, PersonInput
from xolotl.modules.screening.catalog.subscriptions import (
    catch_up,
    reconcile_subscriptions,
)
from xolotl.modules.screening.composition import register_screening
from xolotl.modules.screening.engine.enums import (
    BatchKind,
    BatchStatus,
    NameKind,
    RunMode,
    SuggestedDisposition,
    TriggerType,
)
from xolotl.modules.screening.engine.holder import set_index_holder
from xolotl.modules.screening.engine.models import (
    PartyNameIndex,
    ScreeningBatch,
    ScreeningListSetMember,
    ScreeningMatch,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.engine.policy import today_mx
from xolotl.modules.screening.engine.screen import (
    RunResult,
    ScreenOptions,
    Trigger,
    screen,
)
from xolotl.modules.screening.events import (
    LISTS_VERSION_PUBLISHED,
    MATCH_DELISTING_SUGGESTED,
    PRIVATE_LIST_VERSION_ACTIVATED,
)
from xolotl.modules.screening.lists import (
    EntityType,
    EntryName,
    ListEntry,
    NameQuality,
    PartialDate,
)
from xolotl.modules.screening.matching import MATCHER_VERSION
from xolotl.modules.screening.rescreen import (
    ensure_screening_index,
    register_rescreen,
    walkers,
)
from xolotl.modules.screening.rescreen.list_delta import LIST_DELTA_KIND
from xolotl.modules.screening.rescreen.party_events import SCREENING_CONSUMER
from xolotl.modules.screening.rescreen.walkers import (
    PERIODIC_RESCREEN_KIND,
    PRIVATE_LIST_RESCREEN_KIND,
    REINDEX_KIND,
    SUPERSEDED_VERSION,
)
from xolotl.modules.tenants import service as tenants_service
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import Settings, get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus, Role
from xolotl.platform.events import OutboxEventBus
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.jobs import JobRunner, PostgresJobQueue
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import Job, job_handler
from xolotl.platform.storage.local import LocalFsObjectStore

LPB = "MX_LPB"
OFAC = "OFAC_SDN"
BORN = date(1970, 5, 10)
JUAN = ("Juan Carlos", "Pérez", "Gómez")
ROSA = ("Rosa Elena", "Martínez", "Ortiz")
FAR = ("Hugo", "Duque", "Pico")
WALK_KINDS = [PRIVATE_LIST_RESCREEN_KIND, PERIODIC_RESCREEN_KIND, REINDEX_KIND]


def _entry(code: str, key: str, name: tuple[str, str, str]) -> ListEntry:
    return ListEntry(
        list_code=code,
        entry_key=key,
        entity_type=EntityType.PERSON,
        primary_name=EntryName(" ".join(name).upper(), NameQuality.PRIMARY),
        birth_dates=(PartialDate(BORN.year, BORN.month, BORN.day),),
        source_ref=f"ref {key}",
    )


def _person(name: tuple[str, str, str]) -> PartyCreateRequest:
    given, first, second = name
    return PartyCreateRequest(
        party_type=PartyType.PERSON,
        person=PersonInput(
            given_names=given,
            first_surname=first,
            second_surname=second,
            birth_date=BORN,
        ),
    )


@dataclass(frozen=True, slots=True)
class Wired:
    tenant: CreatedTenant
    bus: OutboxEventBus
    queue: PostgresJobQueue
    runner: JobRunner
    store: LocalFsObjectStore


@pytest.fixture
def wired(
    tenant: CreatedTenant, admin_engine: Engine, tmp_path: Path
) -> Iterator[Wired]:
    seed_sources(admin_engine)
    assign_itf(tenant, admin_engine)
    factory = get_sessionmaker()
    queue = PostgresJobQueue(factory)
    reset_subscribers()
    _ = register_screening(
        session_factory=factory, settings=get_settings(), queue=queue
    )
    register_rescreen(queue=queue)
    yield Wired(
        tenant,
        OutboxEventBus(factory),
        queue,
        # Only the walks: the party events' screen_party jobs stay queued.
        JobRunner(queue, factory, worker_id="w-test", kinds=WALK_KINDS),
        LocalFsObjectStore(tmp_path / "objects"),
    )
    reset_subscribers()
    set_index_holder(None)


@pytest.fixture
def other_tenant(engine: Engine) -> CreatedTenant:
    return new_tenant("globex", "admin@globex.mx")


@pytest.fixture
def two_per_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the walk pages two parties long."""
    small = get_settings().model_copy(update={"screening_rescreen_batch": 2})

    def settings() -> Settings:
        return small

    monkeypatch.setattr(walkers, "get_settings", settings)


@contextmanager
def _job(tenant: CreatedTenant) -> Generator[Session]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        yield s


def _mlro(tenant: CreatedTenant):
    return staff_session(tenant.tenant_id, tenant.admin_user_id, roles=(Role.MLRO,))


def _add(tenant: CreatedTenant, *names: tuple[str, str, str]) -> list[UUID]:
    with _mlro(tenant) as s:
        return [service.create_party(s, _person(name)).id for name in names]


def _dispatch(wired: Wired) -> None:
    with _job(wired.tenant) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )
    while wired.bus.dispatch_pending():
        pass


def _drain(wired: Wired) -> int:
    ran = 0
    while step := wired.runner.run_once():
        ran += step
    return ran


def _private(wired: Wired, *entries: ListEntry) -> UUID:
    """Upload a version of the tenant's LPB and hand it to the consumer.

    Returns:
        The version's id.

    """
    with _mlro(wired.tenant) as s:
        version_id = load_private(s, wired.store, LPB, entries).id
    _dispatch(wired)
    return version_id


def _batches(tenant: CreatedTenant, kind: BatchKind) -> list[ScreeningBatch]:
    with _job(tenant) as s:
        return list(
            s.scalars(
                select(ScreeningBatch)
                .where(ScreeningBatch.kind == kind)
                .order_by(ScreeningBatch.started_at, ScreeningBatch.id)
            )
        )


def _jobs(tenant: CreatedTenant, kind: str) -> list[JobRow]:
    with _job(tenant) as s:
        return list(
            s.scalars(
                select(JobRow)
                .where(JobRow.kind == kind)
                .order_by(JobRow.created_at, JobRow.id)
            )
        )


def _runs(tenant: CreatedTenant, mode: RunMode) -> dict[UUID, list[ScreeningRun]]:
    """Read the tenant's runs of ``mode``.

    Returns:
        The runs by party.

    """
    with _job(tenant) as s:
        rows = s.execute(
            select(ScreeningSubject.party_id, ScreeningRun)
            .join(ScreeningRun, ScreeningRun.subject_id == ScreeningSubject.id)
            .where(ScreeningRun.mode == mode)
        ).all()
    found: dict[UUID, list[ScreeningRun]] = {}
    for party_id, run in rows:
        assert party_id is not None
        found.setdefault(party_id, []).append(run)
    return found


def _findings(tenant: CreatedTenant) -> dict[UUID, list[ScreeningMatch]]:
    """Read the tenant's findings.

    Returns:
        The findings by party.

    """
    with _job(tenant) as s:
        rows = s.execute(
            select(ScreeningSubject.party_id, ScreeningMatch).join(
                ScreeningMatch, ScreeningMatch.subject_id == ScreeningSubject.id
            )
        ).all()
    found: dict[UUID, list[ScreeningMatch]] = {}
    for party_id, match in rows:
        assert party_id is not None
        found.setdefault(party_id, []).append(match)
    return found


def _events(tenant: CreatedTenant, event_type: str) -> int:
    with _job(tenant) as s:
        return (
            s.scalar(
                select(func.count())
                .select_from(OutboxRow)
                .where(OutboxRow.type == event_type)
            )
            or 0
        )


def _replay(wired: Wired, event_type: str) -> int:
    return wired.bus.replay(
        since=clock.now() - timedelta(hours=1),
        types=[event_type],
        consumer=SCREENING_CONSUMER,
        tenant_id=wired.tenant.tenant_id,
    )


def _make_due(tenant: CreatedTenant) -> None:
    with _job(tenant) as s:
        _ = s.execute(
            update(JobRow)
            .where(JobRow.status == JobStatus.PENDING)
            .values(run_after=clock.now() - timedelta(minutes=1))
        )


# ---- private-list versions ---------------------------------------------------------


def test_a_private_list_version_rescreens_the_whole_base(wired: Wired):
    juan, rosa, far = _add(wired.tenant, JUAN, ROSA, FAR)
    _dispatch(wired)

    v1 = _private(wired, _entry(LPB, "L1", JUAN))
    assert _drain(wired) == 1

    runs = _runs(wired.tenant, RunMode.PRIVATE_LIST)
    assert set(runs) == {juan, rosa, far}
    assert all(len(each) == 1 for each in runs.values())
    (batch,) = _batches(wired.tenant, BatchKind.PRIVATE_LIST)
    assert (batch.ref, batch.status, batch.total, batch.done, batch.runs) == (
        str(v1),
        BatchStatus.COMPLETED,
        3,
        3,
        3,
    )
    assert all(
        r.restricted_to_delta
        and r.batch_id == batch.id
        and r.trigger_type is TriggerType.JOB
        for each in runs.values()
        for r in each
    )
    findings = _findings(wired.tenant)
    assert set(findings) == {juan}
    assert {m.list_code for m in findings[juan]} == {LPB}
    assert batch.new_matches == 1

    _ = _private(wired, _entry(LPB, "L1", JUAN), _entry(LPB, "L2", ROSA))
    _ = _drain(wired)

    findings = _findings(wired.tenant)
    # The unchanged entry's finding is deduplicated; only the added one is new.
    assert set(findings) == {juan, rosa}
    assert [len(findings[juan]), len(findings[rosa])] == [1, 1]
    second = _batches(wired.tenant, BatchKind.PRIVATE_LIST)[1]
    assert (second.status, second.runs, second.new_matches) == (
        BatchStatus.COMPLETED,
        3,
        1,
    )


def test_a_removed_private_entry_suggests_delisting(wired: Wired):
    juan, rosa = _add(wired.tenant, JUAN, ROSA)
    _dispatch(wired)
    _ = _private(wired, _entry(LPB, "L1", JUAN), _entry(LPB, "L2", ROSA))
    _ = _drain(wired)

    _ = _private(wired, _entry(LPB, "L1", JUAN))
    _ = _drain(wired)

    findings = _findings(wired.tenant)
    (kept,) = findings[juan]
    (removed,) = findings[rosa]
    assert kept.suggested_disposition is None
    assert removed.suggested_disposition is SuggestedDisposition.DELISTED
    assert _events(wired.tenant, MATCH_DELISTING_SUGGESTED) == 1


def test_a_private_list_batch_screens_only_its_tenants_parties(
    wired: Wired, other_tenant: CreatedTenant
):
    (juan,) = _add(wired.tenant, JUAN)
    (theirs,) = _add(other_tenant, JUAN)
    _dispatch(wired)

    _ = _private(wired, _entry(LPB, "L1", JUAN))
    _ = _drain(wired)

    assert set(_runs(wired.tenant, RunMode.PRIVATE_LIST)) == {juan}
    with _job(other_tenant) as s:
        assert s.scalar(select(func.count()).select_from(ScreeningRun)) == 0
        assert s.scalar(select(func.count()).select_from(ScreeningBatch)) == 0
        assert s.scalar(select(func.count()).select_from(ScreeningMatch)) == 0
    assert theirs not in _runs(wired.tenant, RunMode.PRIVATE_LIST)


def test_a_superseded_version_stops_its_batch(wired: Wired):
    juan, far = _add(wired.tenant, JUAN, FAR)
    _dispatch(wired)
    v1 = _private(wired, _entry(LPB, "L1", JUAN))
    v2 = _private(wired, _entry(LPB, "L1", JUAN), _entry(LPB, "L2", ROSA))

    _ = _drain(wired)

    first, second = _batches(wired.tenant, BatchKind.PRIVATE_LIST)
    assert (first.ref, first.status, first.last_error, first.runs) == (
        str(v1),
        BatchStatus.COMPLETED,
        SUPERSEDED_VERSION,
        0,
    )
    assert (second.ref, second.status, second.last_error, second.runs) == (
        str(v2),
        BatchStatus.COMPLETED,
        None,
        2,
    )
    runs = _runs(wired.tenant, RunMode.PRIVATE_LIST)
    assert {p: [r.batch_id for r in rs] for p, rs in runs.items()} == {
        juan: [second.id],
        far: [second.id],
    }


def test_the_activation_event_opens_one_private_list_batch(wired: Wired):
    version_id = _private(wired, _entry(LPB, "L1", JUAN))

    (batch,) = _batches(wired.tenant, BatchKind.PRIVATE_LIST)
    (page,) = _jobs(wired.tenant, PRIVATE_LIST_RESCREEN_KIND)
    assert (batch.ref, batch.status, batch.total) == (
        str(version_id),
        BatchStatus.RUNNING,
        0,
    )
    assert page.key == f"{PRIVATE_LIST_RESCREEN_KIND}:{batch.id}:start"
    assert page.payload == {
        "batch_id": str(batch.id),
        "private_list_version_id": str(version_id),
        "after_party_id": None,
    }

    assert _replay(wired, PRIVATE_LIST_VERSION_ACTIVATED) == 1

    assert [b.id for b in _batches(wired.tenant, BatchKind.PRIVATE_LIST)] == [batch.id]
    assert [j.id for j in _jobs(wired.tenant, PRIVATE_LIST_RESCREEN_KIND)] == [page.id]

    # Once the batch is complete, a replayed event leaves it alone.
    _ = _drain(wired)
    assert _replay(wired, PRIVATE_LIST_VERSION_ACTIVATED) == 1

    (done,) = _batches(wired.tenant, BatchKind.PRIVATE_LIST)
    assert (done.id, done.status) == (batch.id, BatchStatus.COMPLETED)
    assert [j.id for j in _jobs(wired.tenant, PRIVATE_LIST_RESCREEN_KIND)] == [page.id]


# ---- periodic walks ----------------------------------------------------------------


def _periodic(wired: Wired) -> UUID:
    """Enqueue the schedule's ``{}`` job.

    Returns:
        The job id.

    """
    return enqueue(wired.queue, wired.tenant.tenant_id, kind=PERIODIC_RESCREEN_KIND)


def test_the_periodic_job_opens_one_batch_per_month(
    wired: Wired, monkeypatch: pytest.MonkeyPatch
):
    _ = _periodic(wired)
    assert wired.runner.run_once(limit=1) == 1

    (batch,) = _batches(wired.tenant, BatchKind.PERIODIC)
    month = today_mx().strftime("%Y-%m")
    assert (batch.ref, batch.status) == (month, BatchStatus.RUNNING)

    # A second schedule job in the month finds the batch and its queued page.
    _ = _periodic(wired)
    assert wired.runner.run_once(limit=1) == 1
    assert len(_batches(wired.tenant, BatchKind.PERIODIC)) == 1
    pages = [j for j in _jobs(wired.tenant, PERIODIC_RESCREEN_KIND) if j.payload]
    assert len(pages) == 1

    _ = _drain(wired)
    assert _batches(wired.tenant, BatchKind.PERIODIC)[0].status is (
        BatchStatus.COMPLETED
    )
    # Once complete, the month's batch is never reopened.
    _ = _periodic(wired)
    _ = _drain(wired)
    assert len(_batches(wired.tenant, BatchKind.PERIODIC)) == 1
    assert (
        len([j for j in _jobs(wired.tenant, PERIODIC_RESCREEN_KIND) if j.payload]) == 1
    )

    # 07:30 UTC on 1 January is 01:30 in Mexico City: the new month.
    new_year = datetime(2027, 1, 1, 7, 30, tzinfo=UTC)
    monkeypatch.setattr(clock, "now", lambda: new_year)
    _ = _periodic(wired)
    _ = _drain(wired)
    assert [b.ref for b in _batches(wired.tenant, BatchKind.PERIODIC)] == [
        month,
        "2027-01",
    ]


@pytest.mark.usefixtures("two_per_page")
def test_the_periodic_walk_screens_every_active_party_against_the_full_list_set(
    wired: Wired,
):
    _ = load_shared(OFAC, [_entry(OFAC, "O1", JUAN)], store=wired.store)
    juan, rosa, far, gone = _add(wired.tenant, JUAN, ROSA, FAR, FAR)
    _dispatch(wired)
    _ = _private(wired, _entry(LPB, "L1", ROSA))
    with _mlro(wired.tenant) as s:
        _ = purge_party(s, service.get_party(s, gone))

    _ = _periodic(wired)
    _ = _drain(wired)

    (batch,) = _batches(wired.tenant, BatchKind.PERIODIC)
    assert (batch.status, batch.total, batch.done, batch.runs) == (
        BatchStatus.COMPLETED,
        3,
        3,
        3,
    )
    pages = [j for j in _jobs(wired.tenant, PERIODIC_RESCREEN_KIND) if j.payload]
    ordered = sorted([juan, rosa, far])
    assert [j.key for j in pages] == [
        f"{PERIODIC_RESCREEN_KIND}:{batch.id}:start",
        f"{PERIODIC_RESCREEN_KIND}:{batch.id}:{ordered[1]}",
    ]
    assert batch.cursor == str(ordered[2])
    runs = _runs(wired.tenant, RunMode.PERIODIC)
    assert set(runs) == {juan, rosa, far}
    assert all(len(each) == 1 for each in runs.values())
    assert not any(r.restricted_to_delta for each in runs.values() for r in each)
    with _job(wired.tenant) as s:
        codes = set(
            s.scalars(
                select(ScreeningListSetMember.list_code).where(
                    ScreeningListSetMember.list_set_id == runs[juan][0].list_set_id
                )
            )
        )
    assert {OFAC, LPB} <= codes
    findings = _findings(wired.tenant)
    assert {m.list_code for m in findings[juan]} == {OFAC}
    assert {m.list_code for m in findings[rosa]} == {LPB}
    assert far not in findings


def test_an_initial_version_event_opens_one_periodic_batch(wired: Wired):
    juan, far = _add(wired.tenant, JUAN, FAR)
    report = load_shared(OFAC, [_entry(OFAC, "O1", JUAN)], store=wired.store)
    with _job(wired.tenant) as s:
        _ = reconcile_subscriptions(s, today=date(2026, 10, 19))
        _ = catch_up(s)
    _dispatch(wired)

    (batch,) = _batches(wired.tenant, BatchKind.PERIODIC)
    assert (batch.ref, batch.status, batch.total) == (
        f"initial:{OFAC}",
        BatchStatus.RUNNING,
        2,
    )
    (page,) = _jobs(wired.tenant, PERIODIC_RESCREEN_KIND)
    assert page.payload == {
        "batch_id": str(batch.id),
        "require_versions": {OFAC: report.seq},
        "after_party_id": None,
    }
    assert _jobs(wired.tenant, LIST_DELTA_KIND) == []
    assert _batches(wired.tenant, BatchKind.LIST_DELTA) == []

    assert _replay(wired, LISTS_VERSION_PUBLISHED) >= 1

    assert len(_batches(wired.tenant, BatchKind.PERIODIC)) == 1
    assert len(_jobs(wired.tenant, PERIODIC_RESCREEN_KIND)) == 1

    _ = _drain(wired)

    runs = _runs(wired.tenant, RunMode.PERIODIC)
    assert set(runs) == {juan, far}
    assert {m.list_code for m in _findings(wired.tenant)[juan]} == {OFAC}
    assert _batches(wired.tenant, BatchKind.PERIODIC)[0].status is (
        BatchStatus.COMPLETED
    )


@pytest.mark.usefixtures("two_per_page")
def test_a_walker_resumes_from_its_cursor_after_a_crash(
    wired: Wired, monkeypatch: pytest.MonkeyPatch
):
    parties = _add(wired.tenant, JUAN, ROSA, FAR, FAR)
    _dispatch(wired)
    real = screen
    calls = 0

    def crash_on_the_third(
        s: Session,
        subject: ScreeningSubject,
        *,
        mode: RunMode,
        trigger: Trigger,
        **options: Unpack[ScreenOptions],
    ) -> RunResult:
        nonlocal calls
        calls += 1
        if calls == 3:
            msg = "worker died"
            raise RuntimeError(msg)
        return real(s, subject, mode=mode, trigger=trigger, **options)

    monkeypatch.setattr(walkers, "screen", crash_on_the_third)

    _ = _periodic(wired)
    _ = _drain(wired)
    # The second page failed on its first party and waits for its retry.
    (batch,) = _batches(wired.tenant, BatchKind.PERIODIC)
    assert (batch.status, batch.done, batch.cursor) == (
        BatchStatus.RUNNING,
        2,
        str(sorted(parties)[1]),
    )
    _make_due(wired.tenant)
    _ = _drain(wired)

    (batch,) = _batches(wired.tenant, BatchKind.PERIODIC)
    assert (batch.status, batch.done, batch.runs) == (BatchStatus.COMPLETED, 4, 4)
    runs = _runs(wired.tenant, RunMode.PERIODIC)
    assert set(runs) == set(parties)
    assert all([r.batch_id for r in each] == [batch.id] for each in runs.values())


@pytest.mark.usefixtures("two_per_page")
def test_a_failed_walk_resumes_from_its_cursor_when_reopened(
    wired: Wired, monkeypatch: pytest.MonkeyPatch
):
    parties = sorted(_add(wired.tenant, JUAN, ROSA, FAR))
    _dispatch(wired)
    _ = _periodic(wired)
    assert wired.runner.run_once(limit=2) == 2  # the {} job and page one
    real = screen

    def broken(*_args: object, **_kwargs: object) -> None:
        msg = "screening is down"
        raise RuntimeError(msg)

    monkeypatch.setattr(walkers, "screen", broken)
    with _job(wired.tenant) as s:
        _ = s.execute(
            update(JobRow)
            .where(JobRow.status == JobStatus.PENDING)
            .values(max_attempts=1)
        )
    _ = _drain(wired)
    (batch,) = _batches(wired.tenant, BatchKind.PERIODIC)
    assert (batch.status, batch.last_error, batch.cursor) == (
        BatchStatus.FAILED,
        "JOB_FAILED:RuntimeError",
        str(parties[1]),
    )

    monkeypatch.setattr(walkers, "screen", real)
    _ = _periodic(wired)
    _ = _drain(wired)

    (batch,) = _batches(wired.tenant, BatchKind.PERIODIC)
    assert (batch.status, batch.done, batch.last_error) == (
        BatchStatus.COMPLETED,
        3,
        None,
    )
    pages = [j for j in _jobs(wired.tenant, PERIODIC_RESCREEN_KIND) if j.payload]
    assert pages[-1].payload["after_party_id"] == str(parties[1])
    runs = _runs(wired.tenant, RunMode.PERIODIC)
    assert {p: len(rs) for p, rs in runs.items()} == dict.fromkeys(parties, 1)


def _redeliver(wired: Wired, row: JobRow) -> None:
    """Hand a page's job to its handler again, as a redelivery would."""
    job = Job(
        id=row.id,
        tenant_id=row.tenant_id,
        kind=row.kind,
        key=row.key,
        payload=row.payload,
        attempts=row.attempts + 1,
        max_attempts=row.max_attempts,
        lock_token=uuid4(),
    )
    with _job(wired.tenant) as s:
        job_handler(row.kind)(s, job)


def _replay_page_one(wired: Wired, kind: str, batch_kind: BatchKind, mode: RunMode):
    """Redeliver a running batch's walked first page; assert it changes nothing."""
    (before,) = _batches(wired.tenant, batch_kind)
    assert before.status is BatchStatus.RUNNING
    jobs = _jobs(wired.tenant, kind)
    (first,) = [j for j in jobs if j.key == f"{kind}:{before.id}:start"]
    assert first.status is JobStatus.DONE
    runs = sum(len(r) for r in _runs(wired.tenant, mode).values())
    assert runs == 2

    _redeliver(wired, first)

    (after,) = _batches(wired.tenant, batch_kind)
    assert (after.status, after.done, after.runs, after.cursor) == (
        BatchStatus.RUNNING,
        before.done,
        before.runs,
        before.cursor,
    )
    assert sum(len(r) for r in _runs(wired.tenant, mode).values()) == runs
    assert [j.id for j in _jobs(wired.tenant, kind)] == [j.id for j in jobs]


@pytest.mark.usefixtures("two_per_page")
def test_a_redelivered_first_periodic_page_of_a_running_batch_is_skipped(
    wired: Wired,
):
    _ = _add(wired.tenant, JUAN, ROSA, FAR)
    _dispatch(wired)
    _ = _periodic(wired)
    assert wired.runner.run_once(limit=2) == 2  # the {} job and page one

    _replay_page_one(
        wired, PERIODIC_RESCREEN_KIND, BatchKind.PERIODIC, RunMode.PERIODIC
    )


@pytest.mark.usefixtures("two_per_page")
def test_a_redelivered_first_private_list_page_of_a_running_batch_is_skipped(
    wired: Wired,
):
    _ = _add(wired.tenant, JUAN, ROSA, FAR)
    _dispatch(wired)
    _ = _private(wired, _entry(LPB, "L1", JUAN))
    assert wired.runner.run_once(limit=1) == 1

    _replay_page_one(
        wired, PRIVATE_LIST_RESCREEN_KIND, BatchKind.PRIVATE_LIST, RunMode.PRIVATE_LIST
    )


@pytest.mark.usefixtures("two_per_page")
def test_a_redelivered_first_reindex_page_of_a_running_batch_is_skipped(
    wired: Wired,
):
    _ = _add(wired.tenant, JUAN, ROSA, FAR)
    _dispatch(wired)
    with _job(wired.tenant) as s:
        _ = s.execute(update(PartyNameIndex).values(matcher_version="1.0.0"))
    assert ensure_screening_index(get_sessionmaker(), wired.queue) == 1
    assert wired.runner.run_once(limit=1) == 1

    _replay_page_one(wired, REINDEX_KIND, BatchKind.REINDEX, RunMode.REINDEX)


@pytest.mark.usefixtures("two_per_page")
def test_a_redelivered_walker_page_changes_nothing(wired: Wired):
    _ = _add(wired.tenant, JUAN, ROSA, FAR)
    _dispatch(wired)
    _ = _periodic(wired)
    _ = _drain(wired)
    (before,) = _batches(wired.tenant, BatchKind.PERIODIC)
    runs = sum(len(r) for r in _runs(wired.tenant, RunMode.PERIODIC).values())

    for row in [j for j in _jobs(wired.tenant, PERIODIC_RESCREEN_KIND) if j.payload]:
        _redeliver(wired, row)

    (after,) = _batches(wired.tenant, BatchKind.PERIODIC)
    assert (after.done, after.runs, after.cursor) == (
        before.done,
        before.runs,
        before.cursor,
    )
    assert sum(len(r) for r in _runs(wired.tenant, RunMode.PERIODIC).values()) == runs


@pytest.mark.usefixtures("two_per_page")
def test_a_manual_rescreen_carries_its_cli_trigger_on_every_page(wired: Wired):
    parties = sorted(_add(wired.tenant, JUAN, ROSA, FAR))
    _dispatch(wired)
    cli = Trigger(TriggerType.CLI, "cli:tester")
    with _job(wired.tenant) as s:
        batch_id = walkers.start_rescreen(
            s, wired.queue, kind=BatchKind.PERIODIC, trigger=cli
        )
    _ = _drain(wired)

    (batch,) = _batches(wired.tenant, BatchKind.PERIODIC)
    assert (batch.id, batch.status, batch.runs) == (
        batch_id,
        BatchStatus.COMPLETED,
        3,
    )
    pages = _jobs(wired.tenant, PERIODIC_RESCREEN_KIND)
    assert [j.payload for j in pages] == [
        {
            "batch_id": str(batch.id),
            "trigger_type": "CLI",
            "trigger_ref": "cli:tester",
            "after_party_id": after,
        }
        for after in (None, str(parties[1]))
    ]
    runs = _runs(wired.tenant, RunMode.PERIODIC)
    assert {
        (r.trigger_type, r.trigger_ref) for each in runs.values() for r in each
    } == {(TriggerType.CLI, "cli:tester")}


# ---- re-index ----------------------------------------------------------------------


def _index_versions(tenant: CreatedTenant) -> dict[UUID, str]:
    with _job(tenant) as s:
        rows = s.execute(
            select(PartyNameIndex.party_id, PartyNameIndex.matcher_version).where(
                PartyNameIndex.name_kind == NameKind.LEGAL
            )
        ).all()
    return dict(rows)


def test_ensure_screening_index_opens_a_reindex_for_a_new_matcher_version(
    wired: Wired,
):
    parties = _add(wired.tenant, JUAN, FAR)
    _dispatch(wired)
    factory = get_sessionmaker()
    # A current index needs nothing.
    assert ensure_screening_index(factory, wired.queue) == 0
    assert _batches(wired.tenant, BatchKind.REINDEX) == []
    with _job(wired.tenant) as s:
        _ = s.execute(update(PartyNameIndex).values(matcher_version="1.0.0"))

    assert ensure_screening_index(factory, wired.queue) == 1
    assert ensure_screening_index(factory, wired.queue) == 0

    (batch,) = _batches(wired.tenant, BatchKind.REINDEX)
    assert (batch.ref, batch.status, batch.total) == (
        MATCHER_VERSION,
        BatchStatus.RUNNING,
        2,
    )
    assert len(_jobs(wired.tenant, REINDEX_KIND)) == 1

    _ = _drain(wired)

    assert _index_versions(wired.tenant) == dict.fromkeys(parties, MATCHER_VERSION)
    runs = _runs(wired.tenant, RunMode.REINDEX)
    assert {p: len(rs) for p, rs in runs.items()} == dict.fromkeys(parties, 1)
    assert _batches(wired.tenant, BatchKind.REINDEX)[0].status is (
        BatchStatus.COMPLETED
    )
    assert ensure_screening_index(factory, wired.queue) == 0


def test_ensure_screening_index_backfills_parties_without_an_index_row(
    wired: Wired,
):
    parties = _add(wired.tenant, JUAN, FAR)
    # Parties made before the screening subscriber existed: no index row.
    with _job(wired.tenant) as s:
        _ = s.execute(delete(PartyNameIndex))
    assert _index_versions(wired.tenant) == {}

    assert ensure_screening_index(get_sessionmaker(), wired.queue) == 1
    _ = _drain(wired)

    assert _index_versions(wired.tenant) == dict.fromkeys(parties, MATCHER_VERSION)
    assert set(_runs(wired.tenant, RunMode.REINDEX)) == set(parties)


def test_the_worker_start_runs_the_index_check(wired: Wired):
    _ = _add(wired.tenant, JUAN)

    assert build_worker().run(max_ticks=0) == 0

    (batch,) = _batches(wired.tenant, BatchKind.REINDEX)
    assert (batch.ref, batch.status) == (MATCHER_VERSION, BatchStatus.RUNNING)
    assert len(_jobs(wired.tenant, REINDEX_KIND)) == 1


def test_the_schedule_names_the_handlers_kind():
    assert tenants_service.SCREENING_PERIODIC_SCHEDULE == PERIODIC_RESCREEN_KIND
