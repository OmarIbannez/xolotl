"""The shared list-delta re-screen job (SP-03 spec §10.2, FR-SC-003; F3).

A delta re-screens only the parties Postgres blocking puts near its added and
changed entries, against those entries only, and suggests ``DELISTED`` on the
findings of removed and non-screenable ones. A page that runs out of attempts fails
its batch, and the next open of the batch resumes from its cursor.
"""

import dataclasses
import logging
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.orm import Session

from tests.helpers import staff_session, system_ctx
from tests.modules.screening.catalog.support import load_shared, seed_sources
from tests.modules.screening.review.support import assign_itf
from tests.platform.jobs_support import enqueue
from xolotl.modules.party import service
from xolotl.modules.party.enums import IdentifierType, PartyType
from xolotl.modules.party.retention import purge_party
from xolotl.modules.party.schemas import (
    IdentifierInput,
    PartyCreateRequest,
    PersonInput,
)
from xolotl.modules.screening.catalog.fetcher import HttpListFetcher
from xolotl.modules.screening.catalog.models import ListEntryDelta
from xolotl.modules.screening.catalog.poll import LISTS_POLL_KIND, register_lists
from xolotl.modules.screening.catalog.subscriptions import (
    catch_up,
    reconcile_subscriptions,
)
from xolotl.modules.screening.composition import register_screening
from xolotl.modules.screening.engine.enums import (
    BatchKind,
    BatchStatus,
    MatchStatus,
    RunMode,
    SuggestedDisposition,
    TriggerType,
)
from xolotl.modules.screening.engine.holder import set_index_holder
from xolotl.modules.screening.engine.models import (
    ScreeningBatch,
    ScreeningHit,
    ScreeningMatch,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.engine.screen import Trigger, screen
from xolotl.modules.screening.engine.subjects import subject_for_party
from xolotl.modules.screening.events import (
    LISTS_VERSION_PUBLISHED,
    MATCH_DELISTING_SUGGESTED,
)
from xolotl.modules.screening.lists import (
    EntityType,
    EntryIdentifier,
    EntryName,
    IdType,
    ListEntry,
    NameQuality,
    PartialDate,
)
from xolotl.modules.screening.rescreen import blocking, list_delta, register_rescreen
from xolotl.modules.screening.rescreen.batches import fail_batch, lock_batch
from xolotl.modules.screening.rescreen.list_delta import LIST_DELTA_KIND
from xolotl.modules.screening.rescreen.party_events import SCREENING_CONSUMER
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import Settings, get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus, Role
from xolotl.platform.events import InAppNotifier, OutboxEventBus
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.jobs import JobRunner, PostgresJobQueue
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import Job, job_handler
from xolotl.platform.storage.local import LocalFsObjectStore

OFAC = "OFAC_SDN"
BORN = date(1970, 5, 10)
NAMES = {
    "U": ("Juan Carlos", "Pérez", "Gómez"),
    "A": ("Rosa Elena", "Martínez", "Ortiz"),
    "C": ("Miguel Ángel", "Torres", "Ruiz"),
    "R": ("Laura Patricia", "Vega", "Salinas"),
    "D": ("Fernando José", "Navarro", "Ibarra"),
}
# Shares no blocking key and no trigrams with the added and changed entries.
FAR = ("Hugo", "Duque", "Pico")
RFC = "SAGC800101AB8"
CARLOS = ("Carlos", "Sánchez", "Gil")


def _entry(key: str, name: tuple[str, str, str]) -> ListEntry:
    return ListEntry(
        list_code=OFAC,
        entry_key=key,
        entity_type=EntityType.PERSON,
        primary_name=EntryName(" ".join(name).upper(), NameQuality.PRIMARY),
        birth_dates=(PartialDate(BORN.year, BORN.month, BORN.day),),
        source_ref=f"sdn {key}",
    )


def _person(
    name: tuple[str, str, str], *, born: date | None = BORN, rfc: str | None = None
) -> PartyCreateRequest:
    given, first, second = name
    return PartyCreateRequest(
        party_type=PartyType.PERSON,
        person=PersonInput(
            given_names=given,
            first_surname=first,
            second_surname=second,
            birth_date=born,
        ),
        identifiers=[IdentifierInput(id_type=IdentifierType.RFC, value=rfc)]
        if rfc
        else [],
    )


# Unchanged entries near no party, so removing R stays under the mass-removal guard.
FILLER = [
    _entry(f"F{i}", name)
    for i, name in enumerate([
        ("Gilberto", "Aranda", "Bustos"),
        ("Hortensia", "Carvajal", "Duarte"),
        ("Isidro", "Echeverría", "Fuentes"),
        ("Jacinta", "Galindo", "Herrera"),
    ])
]
V1 = [_entry(key, NAMES[key]) for key in ("C", "D", "R", "U")] + FILLER
V2 = [
    _entry("A", NAMES["A"]),
    dataclasses.replace(_entry("C", NAMES["C"]), programs=("SDGT",)),
    dataclasses.replace(
        _entry("D", NAMES["D"]), status="DESVIRTUADO", screenable=False
    ),
    _entry("U", NAMES["U"]),
    *FILLER,
]
# Five entries near no party, each changed by v2.
PAGED = [
    ("Abel", "Quintero", "Zamora"),
    ("Beatriz", "Olmedo", "Yáñez"),
    ("Ciro", "Pacheco", "Villalba"),
    ("Dora", "Ugalde", "Tamayo"),
    ("Efraín", "Rentería", "Sosa"),
]


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
        # Only the delta pages: the party events' screen_party jobs stay queued.
        JobRunner(queue, factory, worker_id="w-test", kinds=[LIST_DELTA_KIND]),
        LocalFsObjectStore(tmp_path / "objects"),
    )
    reset_subscribers()
    set_index_holder(None)


@pytest.fixture
def two_per_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the delta pages two entries long."""
    small = get_settings().model_copy(update={"screening_delta_batch": 2})

    def settings() -> Settings:
        return small

    monkeypatch.setattr(list_delta, "get_settings", settings)


@contextmanager
def _job(tenant: CreatedTenant) -> Generator[Session]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        yield s


def _add(tenant: CreatedTenant, *bodies: PartyCreateRequest) -> list[UUID]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id, roles=(Role.MLRO,)) as s:
        return [service.create_party(s, body).id for body in bodies]


def _dispatch(wired: Wired) -> None:
    with _job(wired.tenant) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )
    while wired.bus.dispatch_pending():
        pass


def _hand_over(wired: Wired, entries: list[ListEntry]) -> UUID:
    """Load the next version and hand it over, as the tenant half of lists.poll does.

    Returns:
        The new version's id.

    """
    version_id = load_shared(OFAC, entries, store=wired.store).version_id
    assert version_id is not None
    with _job(wired.tenant) as s:
        _ = reconcile_subscriptions(s, today=date(2026, 10, 19))
        _ = catch_up(s)
    _dispatch(wired)
    return version_id


def _screen_all(tenant: CreatedTenant, party_ids: list[UUID]) -> None:
    """Screen each party against every list, as an earlier periodic run would."""
    with _job(tenant) as s:
        for party_id in party_ids:
            _ = screen(
                s,
                subject_for_party(s, party_id),
                mode=RunMode.PERIODIC,
                trigger=Trigger(TriggerType.JOB),
            )


def _drain(wired: Wired) -> int:
    ran = 0
    while step := wired.runner.run_once():
        ran += step
    return ran


@dataclass(frozen=True, slots=True)
class Base:
    parties: dict[str, UUID]
    far: UUID
    version_id: UUID


def _base(wired: Wired) -> Base:
    """Parties near U, A, C, R and D and one far from all; v1, then the v2 delta.

    Before the delta, U, C, R and D each have an open finding.

    Returns:
        The party ids by entry key, the far party and v2's id.

    """
    keys = sorted(NAMES)
    *near, far = _add(wired.tenant, *(_person(NAMES[k]) for k in keys), _person(FAR))
    parties = dict(zip(keys, near, strict=True))
    _ = _hand_over(wired, V1)
    _screen_all(wired.tenant, [parties[k] for k in ("C", "D", "R", "U")])
    assert set(_open_keys(wired.tenant)) == {"C", "D", "R", "U"}
    return Base(parties, far, _hand_over(wired, V2))


def _paged(wired: Wired) -> UUID:
    """Load five entries near no party, then a version that changes all five.

    Returns:
        The second version's id.

    """
    first = [_entry(f"P{i}", name) for i, name in enumerate(PAGED)]
    _ = _hand_over(wired, first)
    return _hand_over(
        wired, [dataclasses.replace(e, source_ref="moved") for e in first]
    )


def _open_keys(tenant: CreatedTenant) -> list[str]:
    with _job(tenant) as s:
        return sorted(
            s.scalars(
                select(ScreeningMatch.source_entry_key).where(
                    ScreeningMatch.status == MatchStatus.OPEN
                )
            )
        )


def _matches(tenant: CreatedTenant) -> list[ScreeningMatch]:
    with _job(tenant) as s:
        return list(
            s.scalars(
                select(ScreeningMatch).order_by(
                    ScreeningMatch.created_at, ScreeningMatch.id
                )
            )
        )


def _batch(tenant: CreatedTenant) -> ScreeningBatch:
    with _job(tenant) as s:
        return s.scalars(select(ScreeningBatch)).one()


def _delta_runs(tenant: CreatedTenant) -> list[ScreeningRun]:
    with _job(tenant) as s:
        return list(
            s.scalars(
                select(ScreeningRun)
                .where(ScreeningRun.mode == RunMode.LIST_DELTA)
                .order_by(ScreeningRun.created_at, ScreeningRun.id)
            )
        )


def _screened_parties(tenant: CreatedTenant) -> set[UUID]:
    with _job(tenant) as s:
        found = s.scalars(
            select(ScreeningSubject.party_id)
            .join(ScreeningRun, ScreeningRun.subject_id == ScreeningSubject.id)
            .where(ScreeningRun.mode == RunMode.LIST_DELTA)
        )
        return {party_id for party_id in found if party_id is not None}


def _delta_jobs(tenant: CreatedTenant) -> list[JobRow]:
    with _job(tenant) as s:
        return list(
            s.scalars(
                select(JobRow)
                .where(JobRow.kind == LIST_DELTA_KIND)
                .order_by(JobRow.created_at, JobRow.id)
            )
        )


def _suggestion_events(tenant: CreatedTenant) -> int:
    with _job(tenant) as s:
        return (
            s.scalar(
                select(func.count())
                .select_from(OutboxRow)
                .where(OutboxRow.type == MATCH_DELISTING_SUGGESTED)
            )
            or 0
        )


def _rerun(tenant: CreatedTenant, row: JobRow) -> None:
    """Deliver a page job again, after it already committed."""
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
    with _job(tenant) as s:
        job_handler(LIST_DELTA_KIND)(s, job)


def _replay_versions(wired: Wired) -> int:
    return wired.bus.replay(
        since=clock.now() - timedelta(hours=1),
        types=[LISTS_VERSION_PUBLISHED],
        consumer=SCREENING_CONSUMER,
        tenant_id=wired.tenant.tenant_id,
    )


def _counts(tenant: CreatedTenant) -> tuple[int, ...]:
    with _job(tenant) as s:
        runs, hits, matches = (
            s.scalar(select(func.count()).select_from(model)) or 0
            for model in (ScreeningRun, ScreeningHit, ScreeningMatch)
        )
        batch = s.scalars(select(ScreeningBatch)).one()
        return (runs, hits, matches, batch.done, batch.runs, batch.new_matches)


def test_a_delta_alerts_only_on_added_and_changed_entries(wired: Wired):
    base = _base(wired)
    before = {m.source_entry_key: m for m in _matches(wired.tenant)}

    assert _drain(wired) == 1

    matches = {(m.source_entry_key, m.status): m for m in _matches(wired.tenant)}
    new = [
        m for m in _matches(wired.tenant) if m.id not in {b.id for b in before.values()}
    ]
    assert sorted(m.source_entry_key for m in new) == ["A", "C"]
    old_c = matches["C", MatchStatus.SUPERSEDED]
    assert old_c.id == before["C"].id
    assert old_c.superseded_by_match_id == matches["C", MatchStatus.OPEN].id
    untouched = matches["U", MatchStatus.OPEN]
    assert untouched.id == before["U"].id
    assert untouched.suggested_disposition is None
    batch = _batch(wired.tenant)
    assert (batch.kind, batch.ref, batch.status) == (
        BatchKind.LIST_DELTA,
        str(base.version_id),
        BatchStatus.COMPLETED,
    )
    assert (batch.total, batch.done, batch.new_matches, batch.cursor) == (4, 2, 2, "2")
    assert batch.last_error is None
    runs = _delta_runs(wired.tenant)
    # With a list this small every key is rare, so blocking also puts parties near
    # unchanged entries next to A or C; they are screened against A and C only.
    assert batch.runs == len(runs)
    assert all(r.restricted_to_delta and r.batch_id == batch.id for r in runs)
    assert {r.trigger_type for r in runs} == {TriggerType.JOB}
    assert {base.parties["A"], base.parties["C"]} <= _screened_parties(wired.tenant)
    with _job(wired.tenant) as s:
        delta_rows = set(
            s.scalars(
                select(ListEntryDelta.new_entry_id).where(
                    ListEntryDelta.version_id == base.version_id
                )
            )
        )
        hit_entries = set(
            s.scalars(
                select(ScreeningHit.list_entry_id).where(
                    ScreeningHit.run_id.in_([r.id for r in runs])
                )
            )
        )
    assert hit_entries
    assert hit_entries <= delta_rows


def test_a_removed_or_desvirtuado_entry_suggests_delisting_once(wired: Wired):
    _ = _base(wired)

    _ = _drain(wired)

    suggested = {
        m.source_entry_key
        for m in _matches(wired.tenant)
        if m.suggested_disposition is SuggestedDisposition.DELISTED
    }
    assert suggested == {"D", "R"}
    assert _suggestion_events(wired.tenant) == 2

    (page,) = _delta_jobs(wired.tenant)
    _rerun(wired.tenant, page)

    assert _suggestion_events(wired.tenant) == 2


def test_parties_far_from_every_entry_are_not_screened(wired: Wired):
    base = _base(wired)

    _ = _drain(wired)

    assert base.far not in _screened_parties(wired.tenant)
    assert _delta_runs(wired.tenant)


@pytest.mark.usefixtures("two_per_page")
def test_pages_chain_until_the_delta_is_done(wired: Wired):
    _ = _paged(wired)

    assert _drain(wired) == 3

    jobs = _delta_jobs(wired.tenant)
    batch = _batch(wired.tenant)
    assert [j.payload["offset"] for j in jobs] == [0, 2, 4]
    assert [j.key for j in jobs] == [
        f"{LIST_DELTA_KIND}:{batch.id}:{offset}" for offset in (0, 2, 4)
    ]
    assert {j.status for j in jobs} == {JobStatus.DONE}
    assert (batch.status, batch.cursor, batch.total, batch.done, batch.runs) == (
        BatchStatus.COMPLETED,
        "5",
        5,
        5,
        0,
    )


def test_a_redelivered_page_changes_nothing(wired: Wired):
    _ = _base(wired)
    _ = _drain(wired)
    before = _counts(wired.tenant)

    (page,) = _delta_jobs(wired.tenant)
    _rerun(wired.tenant, page)

    assert _counts(wired.tenant) == before


@pytest.mark.usefixtures("two_per_page")
def test_a_redelivered_middle_page_changes_nothing(wired: Wired):
    _ = _paged(wired)
    assert wired.runner.run_once(limit=2) == 2
    first, second, _third = _delta_jobs(wired.tenant)

    _rerun(wired.tenant, first)
    _rerun(wired.tenant, second)
    _ = _drain(wired)

    batch = _batch(wired.tenant)
    assert (batch.status, batch.done, batch.cursor) == (BatchStatus.COMPLETED, 5, "5")
    assert len(_delta_jobs(wired.tenant)) == 3


def test_the_version_event_opens_one_batch_and_enqueues_page_one(wired: Wired):
    base = _base(wired)

    batch = _batch(wired.tenant)
    (page,) = _delta_jobs(wired.tenant)
    assert (batch.kind, batch.ref, batch.status, batch.total) == (
        BatchKind.LIST_DELTA,
        str(base.version_id),
        BatchStatus.RUNNING,
        4,
    )
    assert page.key == f"{LIST_DELTA_KIND}:{batch.id}:0"
    assert page.payload == {
        "batch_id": str(batch.id),
        "version_id": str(base.version_id),
        "offset": 0,
        "overflow": 0,
    }

    assert _replay_versions(wired) == 2

    assert _batch(wired.tenant).id == batch.id
    assert [j.id for j in _delta_jobs(wired.tenant)] == [page.id]


def test_a_completed_batch_is_not_reopened_by_a_redelivered_event(wired: Wired):
    _ = _base(wired)
    _ = _drain(wired)

    _ = _replay_versions(wired)

    assert _batch(wired.tenant).status is BatchStatus.COMPLETED
    assert len(_delta_jobs(wired.tenant)) == 1


def test_an_overflowing_entry_is_left_to_the_periodic_run(
    wired: Wired, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(blocking, "BLOCKING_CAP", 1)
    by_id, by_name = _add(
        wired.tenant,
        _person(CARLOS, born=None, rfc=RFC),
        _person(CARLOS, born=None),
    )
    unchanged = _entry("U", NAMES["U"])
    _ = _hand_over(wired, [unchanged])
    listed = ListEntry(
        list_code=OFAC,
        entry_key="O",
        entity_type=EntityType.PERSON,
        primary_name=EntryName("CARLOS SANCHEZ GIL", NameQuality.PRIMARY),
        identifiers=(EntryIdentifier(id_type=IdType.RFC, value=RFC, normalized=RFC),),
        source_ref="sdn O",
    )
    _ = _hand_over(wired, [listed, unchanged])

    with caplog.at_level(logging.WARNING, logger=list_delta.__name__):
        _ = _drain(wired)

    batch = _batch(wired.tenant)
    assert (batch.status, batch.last_error, batch.done) == (
        BatchStatus.COMPLETED,
        "OVERFLOW:1",
        1,
    )
    screened = _screened_parties(wired.tenant)
    assert by_id in screened
    assert by_name not in screened
    (record,) = [
        r for r in caplog.records if r.getMessage() == "screening.delta_entry_overflow"
    ]
    assert (record.__dict__["list_code"], record.__dict__["entry_key"]) == (OFAC, "O")


def test_purged_parties_are_skipped(wired: Wired):
    base = _base(wired)
    with staff_session(
        wired.tenant.tenant_id, wired.tenant.admin_user_id, roles=(Role.MLRO,)
    ) as s:
        _ = purge_party(s, service.get_party(s, base.parties["A"]))

    _ = _drain(wired)

    screened = _screened_parties(wired.tenant)
    assert base.parties["A"] not in screened
    assert base.parties["C"] in screened
    batch = _batch(wired.tenant)
    assert (batch.status, batch.done, batch.runs) == (
        BatchStatus.COMPLETED,
        2,
        len(screened),
    )


def test_the_catch_up_reaches_the_job_through_the_worker(wired: Wired, tmp_path: Path):
    keys = sorted(NAMES)
    ids = dict(
        zip(keys, _add(wired.tenant, *(_person(NAMES[k]) for k in keys)), strict=True)
    )
    _ = register_lists(
        session_factory=get_sessionmaker(),
        notifier=InAppNotifier(),
        fetcher=HttpListFetcher(timeout_seconds=5, max_bytes=1_000),
        store=LocalFsObjectStore(tmp_path / "fetched"),
        fetching=False,
    )
    worker = JobRunner(
        wired.queue,
        get_sessionmaker(),
        worker_id="w-worker",
        kinds=[LISTS_POLL_KIND, LIST_DELTA_KIND],
    )

    def poll() -> None:
        _ = enqueue(wired.queue, wired.tenant.tenant_id, kind=LISTS_POLL_KIND)
        _ = worker.run_once()
        _dispatch(wired)
        while worker.run_once():
            pass

    _ = load_shared(OFAC, V1, store=wired.store)
    poll()
    _screen_all(wired.tenant, [ids[k] for k in ("C", "D", "R", "U")])
    _ = load_shared(OFAC, V2, store=wired.store)
    poll()

    open_keys = _open_keys(wired.tenant)
    assert open_keys == ["A", "C", "D", "R", "U"]
    assert {ids["A"], ids["C"]} <= _screened_parties(wired.tenant)
    assert _batch(wired.tenant).status is BatchStatus.COMPLETED


# ---- failure and resume ------------------------------------------------------------


@pytest.mark.usefixtures("two_per_page")
def test_a_failed_batch_resumes_from_its_cursor(wired: Wired):
    _ = _paged(wired)
    assert wired.runner.run_once(limit=1) == 1
    first, dead = _delta_jobs(wired.tenant)
    with _job(wired.tenant) as s:
        # The second page ran out of attempts.
        fail_batch(lock_batch(s, _batch(wired.tenant).id), "JOB_FAILED:RuntimeError")
        _ = s.execute(
            update(JobRow).where(JobRow.id == dead.id).values(status=JobStatus.DEAD)
        )

    assert _replay_versions(wired) == 2

    batch = _batch(wired.tenant)
    assert (batch.status, batch.cursor, batch.last_error) == (
        BatchStatus.RUNNING,
        "2",
        None,
    )
    resumed = _delta_jobs(wired.tenant)[2]
    assert resumed.key == f"{LIST_DELTA_KIND}:{batch.id}:2"
    assert resumed.payload["offset"] == 2

    _ = _drain(wired)

    batch = _batch(wired.tenant)
    assert (batch.status, batch.done, batch.cursor, batch.total) == (
        BatchStatus.COMPLETED,
        5,
        "5",
        5,
    )
    assert first.payload["offset"] == 0


def _break_screening(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        msg = "screening is down"
        raise RuntimeError(msg)

    monkeypatch.setattr(list_delta, "screen", broken)


def _set_max_attempts(tenant: CreatedTenant, attempts: int) -> None:
    with _job(tenant) as s:
        _ = s.execute(
            update(JobRow)
            .where(JobRow.kind == LIST_DELTA_KIND)
            .values(max_attempts=attempts)
        )


def test_a_page_out_of_attempts_fails_its_batch(
    wired: Wired, monkeypatch: pytest.MonkeyPatch
):
    _ = _base(wired)
    _break_screening(monkeypatch)
    _set_max_attempts(wired.tenant, 1)

    assert wired.runner.run_once() == 1

    (page,) = _delta_jobs(wired.tenant)
    assert page.status is JobStatus.DEAD
    batch = _batch(wired.tenant)
    assert (batch.status, batch.last_error, batch.done, batch.cursor) == (
        BatchStatus.FAILED,
        "JOB_FAILED:RuntimeError",
        0,
        None,
    )
    # The page's work was rolled back with it.
    assert _suggestion_events(wired.tenant) == 0
    assert _delta_runs(wired.tenant) == []


def test_a_page_with_attempts_left_keeps_its_batch_running(
    wired: Wired, monkeypatch: pytest.MonkeyPatch
):
    _ = _base(wired)
    _break_screening(monkeypatch)
    _set_max_attempts(wired.tenant, 2)

    assert wired.runner.run_once() == 1

    (page,) = _delta_jobs(wired.tenant)
    assert page.status is JobStatus.PENDING
    batch = _batch(wired.tenant)
    assert (batch.status, batch.last_error) == (BatchStatus.RUNNING, None)
