"""Counterparties in the list-delta re-screen and the periodic walk (SP-04 §11.3).

A delta screens the counterparties blocked near its added and changed entries, after
the parties, against those entries only. The periodic walk visits the active parties
and then the counterparties seen in the last twelve months (Q8), one page per job.
Neither reaches a client webhook: a counterparty's screening is staff-only.
"""

import dataclasses
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import starmap
from pathlib import Path
from typing import Unpack
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, text, update
from sqlalchemy.orm import Session

from tests.helpers import staff_session
from tests.modules.screening.catalog.support import load_shared, seed_sources
from tests.modules.screening.counterparty.conftest import job_session
from tests.modules.screening.review.support import assign_itf
from tests.platform.jobs_support import enqueue
from tests.platform.webhooks_support import Stack, deliveries, make_endpoint
from xolotl.modules.party import service
from xolotl.modules.party.enums import PartyType
from xolotl.modules.party.schemas import PartyCreateRequest, PersonInput
from xolotl.modules.screening.catalog.subscriptions import (
    catch_up,
    reconcile_subscriptions,
)
from xolotl.modules.screening.composition import register_screening
from xolotl.modules.screening.counterparty.first_sight import set_first_sight_queue
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.resolve import resolve
from xolotl.modules.screening.counterparty.runs import screen_counterparty
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyInput,
    CounterpartyKind,
)
from xolotl.modules.screening.engine.enums import (
    BatchKind,
    BatchStatus,
    MatchStatus,
    RunMode,
    TriggerType,
)
from xolotl.modules.screening.engine.holder import set_index_holder
from xolotl.modules.screening.engine.models import (
    ScreeningBatch,
    ScreeningMatch,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.engine.screen import RunResult, ScreenOptions, Trigger
from xolotl.modules.screening.events import RUN_COMPLETED, STATUS_CHANGED
from xolotl.modules.screening.lists import (
    EntityType,
    EntryName,
    ListEntry,
    NameQuality,
)
from xolotl.modules.screening.rescreen import register_rescreen, walkers
from xolotl.modules.screening.rescreen.list_delta import LIST_DELTA_KIND
from xolotl.modules.screening.rescreen.walkers import PERIODIC_RESCREEN_KIND
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import Settings, get_settings
from xolotl.platform.db import get_sessionmaker
from xolotl.platform.enums import JobStatus, Role
from xolotl.platform.events import Event, OutboxEventBus
from xolotl.platform.events.event import event_from_row
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.jobs import JobRunner, PostgresJobQueue
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import Job, job_handler
from xolotl.platform.storage.local import LocalFsObjectStore

OFAC = "OFAC_SDN"
NAMES = {
    "U": "Juan Carlos Pérez Gómez",
    "A": "Rosa Elena Martínez Ortiz",
    "C": "Miguel Ángel Torres Ruiz",
    "R": "Laura Patricia Vega Salinas",
    "D": "Fernando José Navarro Ibarra",
}
FAR = "Hugo Duque Pico"
FILLER = [
    ("F0", "Gilberto Aranda Bustos"),
    ("F1", "Hortensia Carvajal Duarte"),
    ("F2", "Isidro Echeverría Fuentes"),
    ("F3", "Jacinta Galindo Herrera"),
]


def _entry(key: str, name: str) -> ListEntry:
    return ListEntry(
        list_code=OFAC,
        entry_key=key,
        entity_type=EntityType.PERSON,
        primary_name=EntryName(name.upper(), NameQuality.PRIMARY),
        source_ref=f"sdn {key}",
    )


FILLER_ENTRIES = list(starmap(_entry, FILLER))
V1 = [_entry(key, NAMES[key]) for key in ("C", "D", "R", "U")] + FILLER_ENTRIES
V2 = [
    _entry("A", NAMES["A"]),
    dataclasses.replace(_entry("C", NAMES["C"]), programs=("SDGT",)),
    dataclasses.replace(
        _entry("D", NAMES["D"]), status="DESVIRTUADO", screenable=False
    ),
    _entry("U", NAMES["U"]),
    *FILLER_ENTRIES,
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
    reset_subscribers()
    # The real webhook fan-out on the bus, so a test can watch a wildcard endpoint.
    stack = Stack(clock.now)
    factory = get_sessionmaker()
    _ = register_screening(
        session_factory=factory, settings=get_settings(), queue=stack.queue
    )
    register_rescreen(queue=stack.queue)
    yield Wired(
        tenant,
        stack.bus,
        stack.queue,
        # The delta pages and the walks only: first-sight jobs stay queued.
        JobRunner(
            stack.queue,
            factory,
            worker_id="w-test",
            kinds=[LIST_DELTA_KIND, PERIODIC_RESCREEN_KIND],
        ),
        LocalFsObjectStore(tmp_path / "objects"),
    )
    reset_subscribers()
    set_index_holder(None)
    set_first_sight_queue(None)


@pytest.fixture
def two_per_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the walk pages two subjects long."""
    small = get_settings().model_copy(update={"screening_rescreen_batch": 2})

    def settings() -> Settings:
        return small

    monkeypatch.setattr(walkers, "get_settings", settings)


def _dispatch(wired: Wired) -> None:
    with job_session(wired.tenant) as s:
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


def _hand_over(wired: Wired, entries: list[ListEntry]) -> UUID:
    version_id = load_shared(OFAC, entries, store=wired.store).version_id
    assert version_id is not None
    with job_session(wired.tenant) as s:
        _ = reconcile_subscriptions(s, today=date(2026, 10, 19))
        _ = catch_up(s)
    _dispatch(wired)
    return version_id


def _lists_ready(wired: Wired) -> None:
    """Hand over v1 and drain its initial walk while the tenant has no subjects."""
    _ = _hand_over(wired, V1)
    assert _drain(wired) == 1
    assert _batch(wired.tenant, BatchKind.PERIODIC).ref == f"initial:{OFAC}"


def _parties(tenant: CreatedTenant, *names: str) -> list[UUID]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id, roles=(Role.MLRO,)) as s:
        ids: list[UUID] = []
        for name in names:
            given, first, second = name.rsplit(" ", 2)
            body = PartyCreateRequest(
                party_type=PartyType.PERSON,
                person=PersonInput(
                    given_names=given, first_surname=first, second_surname=second
                ),
            )
            ids.append(service.create_party(s, body).id)
        return ids


def _counterparties(tenant: CreatedTenant, *names: str) -> list[UUID]:
    with job_session(tenant) as s:
        return [
            resolve(s, CounterpartyInput(kind=CounterpartyKind.PERSON, name=name)).id
            for name in names
        ]


def _screen_counterparties(tenant: CreatedTenant, ids: list[UUID]) -> None:
    """Screen counterparties against every list, as an earlier walk would."""
    with job_session(tenant) as s:
        for counterparty_id in ids:
            _ = screen_counterparty(
                s,
                s.get_one(Counterparty, (tenant.tenant_id, counterparty_id)),
                mode=RunMode.PERIODIC,
                trigger=Trigger(TriggerType.JOB),
            )


def _runs(tenant: CreatedTenant, mode: RunMode) -> dict[UUID, list[ScreeningRun]]:
    """Read the tenant's runs of ``mode`` on counterparty subjects.

    Returns:
        The runs by counterparty.

    """
    with job_session(tenant) as s:
        rows = s.execute(
            select(ScreeningSubject.counterparty_id, ScreeningRun)
            .join(ScreeningRun, ScreeningRun.subject_id == ScreeningSubject.id)
            .where(ScreeningRun.mode == mode)
        ).all()
    found: dict[UUID, list[ScreeningRun]] = {}
    for counterparty_id, run in rows:
        if counterparty_id is not None:
            found.setdefault(counterparty_id, []).append(run)
    return found


def _party_runs(tenant: CreatedTenant, mode: RunMode) -> set[UUID]:
    with job_session(tenant) as s:
        found = s.scalars(
            select(ScreeningSubject.party_id)
            .join(ScreeningRun, ScreeningRun.subject_id == ScreeningSubject.id)
            .where(ScreeningRun.mode == mode)
        )
        return {party_id for party_id in found if party_id is not None}


def _findings(tenant: CreatedTenant) -> list[tuple[UUID, ScreeningMatch]]:
    with job_session(tenant) as s:
        rows = s.execute(
            select(ScreeningSubject.counterparty_id, ScreeningMatch)
            .join(ScreeningMatch, ScreeningMatch.subject_id == ScreeningSubject.id)
            .where(ScreeningSubject.counterparty_id.is_not(None))
            .order_by(ScreeningMatch.created_at, ScreeningMatch.id)
        ).all()
    return [(cp, match) for cp, match in rows if cp is not None]


def _batch(tenant: CreatedTenant, kind: BatchKind) -> ScreeningBatch:
    """Read the tenant's batch of ``kind``; for ``PERIODIC``, the latest one.

    Returns:
        The batch.

    """
    with job_session(tenant) as s:
        return s.scalars(
            select(ScreeningBatch)
            .where(ScreeningBatch.kind == kind)
            .order_by(ScreeningBatch.started_at.desc(), ScreeningBatch.id.desc())
            .limit(1)
        ).one()


def _pages(tenant: CreatedTenant, batch_id: UUID | None = None) -> list[JobRow]:
    """Read the walk's page jobs, of one batch when ``batch_id`` is given.

    Returns:
        The jobs with a payload, oldest first.

    """
    with job_session(tenant) as s:
        rows = s.scalars(
            select(JobRow)
            .where(JobRow.kind == PERIODIC_RESCREEN_KIND)
            .order_by(JobRow.created_at, JobRow.id)
        )
        return [
            row
            for row in rows
            if row.payload
            and (batch_id is None or row.payload["batch_id"] == str(batch_id))
        ]


def _events_of(tenant: CreatedTenant, *types: str) -> list[Event]:
    with job_session(tenant) as s:
        rows = s.scalars(
            select(OutboxRow).where(OutboxRow.type.in_(types)).order_by(OutboxRow.seq)
        ).all()
        return [event_from_row(row) for row in rows]


def _periodic(wired: Wired) -> None:
    _ = enqueue(wired.queue, wired.tenant.tenant_id, kind=PERIODIC_RESCREEN_KIND)


def _make_dormant(tenant: CreatedTenant, counterparty_id: UUID) -> None:
    with job_session(tenant) as s:
        _ = s.execute(
            update(Counterparty)
            .where(Counterparty.id == counterparty_id)
            .values(last_seen_at=clock.now() - timedelta(days=396))
        )


def _redeliver(wired: Wired, row: JobRow) -> None:
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
    with job_session(wired.tenant) as s:
        job_handler(row.kind)(s, job)


# ---- list deltas -------------------------------------------------------------------


def test_a_delta_rescreens_only_the_counterparties_near_its_added_and_changed_entries(
    wired: Wired,
):
    _lists_ready(wired)
    keys = ("U", "A", "C", "R")
    *near, far = _counterparties(wired.tenant, *(NAMES[k] for k in keys), FAR)
    by_key = dict(zip(keys, near, strict=True))
    (party_a,) = _parties(wired.tenant, NAMES["A"])
    _screen_counterparties(wired.tenant, [*near, far])
    before = {match.id for _, match in _findings(wired.tenant)}
    assert len(before) == 3  # U, C and R
    version_id = _hand_over(wired, V2)

    assert _drain(wired) == 1

    new = [(cp, m) for cp, m in _findings(wired.tenant) if m.id not in before]
    assert sorted((m.source_entry_key, cp) for cp, m in new) == sorted([
        ("A", by_key["A"]),
        ("C", by_key["C"]),
    ])
    statuses = {(m.source_entry_key, m.status) for _, m in _findings(wired.tenant)}
    assert ("C", MatchStatus.SUPERSEDED) in statuses
    assert ("U", MatchStatus.OPEN) in statuses
    runs = _runs(wired.tenant, RunMode.LIST_DELTA)
    assert {by_key["A"], by_key["C"]} <= set(runs)
    assert far not in runs
    batch = _batch(wired.tenant, BatchKind.LIST_DELTA)
    assert (batch.ref, batch.status, batch.done) == (
        str(version_id),
        BatchStatus.COMPLETED,
        2,
    )
    delta_runs = [r for each in runs.values() for r in each]
    assert all(r.restricted_to_delta and r.batch_id == batch.id for r in delta_runs)
    assert {r.trigger_type for r in delta_runs} == {TriggerType.JOB}
    # The tenant's parties still get theirs, in the same batch.
    assert party_a in _party_runs(wired.tenant, RunMode.LIST_DELTA)
    assert batch.runs > len(delta_runs)
    assert batch.new_matches == 2 + 1  # the counterparties' A and C, party A


def test_a_delta_with_no_counterparties_behaves_as_before(wired: Wired):
    _lists_ready(wired)
    parties = dict(
        zip("AC", _parties(wired.tenant, NAMES["A"], NAMES["C"]), strict=True)
    )
    _ = _hand_over(wired, V2)

    assert _drain(wired) == 1

    batch = _batch(wired.tenant, BatchKind.LIST_DELTA)
    assert (batch.status, batch.total, batch.done, batch.cursor) == (
        BatchStatus.COMPLETED,
        4,
        2,
        "2",
    )
    assert batch.last_error is None
    assert set(parties.values()) <= _party_runs(wired.tenant, RunMode.LIST_DELTA)
    assert _runs(wired.tenant, RunMode.LIST_DELTA) == {}


def test_a_counterparty_rescreen_reaches_no_webhook(wired: Wired):
    _lists_ready(wired)
    (counterparty,) = _counterparties(wired.tenant, NAMES["A"])
    # A party near entry A in the same tenant: the positive control.
    (party,) = _parties(wired.tenant, NAMES["A"])
    _ = _hand_over(wired, V2)
    # Endpoints made after the hand-over see only what the re-screens publish.
    _ = make_endpoint(wired.tenant.tenant_id, event_types=["*"])

    _ = _drain(wired)
    _periodic(wired)
    _ = _drain(wired)
    _dispatch(wired)

    runs = _runs(wired.tenant, RunMode.LIST_DELTA)
    assert set(runs) == {counterparty}
    assert set(_runs(wired.tenant, RunMode.PERIODIC)) == {counterparty}
    assert [m.source_entry_key for _, m in _findings(wired.tenant)] == ["A"]
    # No webhook-safe screening event names a counterparty subject.
    subjects = {str(r.subject_id) for each in runs.values() for r in each}
    events = _events_of(wired.tenant, RUN_COMPLETED, STATUS_CHANGED)
    assert not [e for e in events if e.data["subject_id"] in subjects]
    # The party's delta run does reach the wildcard endpoint, so the fixture
    # delivers.
    delivered = {d.event_id for d in deliveries(wired.tenant.tenant_id)}
    party_delta = [
        e
        for e in events
        if e.type == RUN_COMPLETED
        and e.data["party_id"] == str(party)
        and e.data["mode"] == RunMode.LIST_DELTA.value
    ]
    assert party_delta
    assert {e.id for e in party_delta} <= delivered


# ---- the periodic walk -------------------------------------------------------------


@pytest.mark.usefixtures("two_per_page")
def test_the_periodic_walk_visits_parties_then_counterparties(wired: Wired):
    _lists_ready(wired)
    parties = sorted(_parties(wired.tenant, NAMES["U"], FAR))
    counterparties = sorted(
        _counterparties(wired.tenant, NAMES["A"], NAMES["C"], NAMES["R"])
    )

    _periodic(wired)
    _ = _drain(wired)

    batch = _batch(wired.tenant, BatchKind.PERIODIC)
    assert (batch.status, batch.total, batch.done, batch.runs) == (
        BatchStatus.COMPLETED,
        5,
        5,
        5,
    )
    assert batch.cursor == f"cp:{counterparties[-1]}"
    prefix = f"{PERIODIC_RESCREEN_KIND}:{batch.id}"
    pages = _pages(wired.tenant, batch.id)
    assert [j.key for j in pages] == [
        f"{prefix}:start",
        f"{prefix}:{parties[1]}",
        f"{prefix}:cp:start",
        f"{prefix}:cp:{counterparties[1]}",
    ]
    assert pages[2].payload == {
        "batch_id": str(batch.id),
        "after_counterparty_id": None,
    }
    runs = _runs(wired.tenant, RunMode.PERIODIC)
    assert {cp: len(each) for cp, each in runs.items()} == dict.fromkeys(
        counterparties, 1
    )
    assert all(r.batch_id == batch.id for each in runs.values() for r in each)
    assert _party_runs(wired.tenant, RunMode.PERIODIC) == set(parties)
    findings = {m.source_entry_key for _, m in _findings(wired.tenant)}
    assert findings == {"C", "R"}


def test_a_dormant_counterparty_is_skipped_by_the_periodic_walk(wired: Wired):
    _lists_ready(wired)
    active, dormant = _counterparties(wired.tenant, NAMES["U"], NAMES["A"])
    _make_dormant(wired.tenant, dormant)

    _periodic(wired)
    _ = _drain(wired)

    batch = _batch(wired.tenant, BatchKind.PERIODIC)
    assert (batch.status, batch.total, batch.done) == (BatchStatus.COMPLETED, 1, 1)
    assert set(_runs(wired.tenant, RunMode.PERIODIC)) == {active}

    # A delta still screens it: blocking reads every counterparty.
    _ = _hand_over(wired, V2)
    _ = _drain(wired)
    assert dormant in _runs(wired.tenant, RunMode.LIST_DELTA)


@pytest.mark.usefixtures("two_per_page")
def test_a_redelivered_counterparty_page_changes_nothing(wired: Wired):
    _lists_ready(wired)
    _ = _counterparties(wired.tenant, NAMES["U"], NAMES["A"], NAMES["C"])
    _periodic(wired)
    _ = _drain(wired)
    before = _batch(wired.tenant, BatchKind.PERIODIC)
    runs = sum(len(r) for r in _runs(wired.tenant, RunMode.PERIODIC).values())
    pages = _pages(wired.tenant)
    assert any("after_counterparty_id" in j.payload for j in pages)

    for row in pages:
        _redeliver(wired, row)

    after = _batch(wired.tenant, BatchKind.PERIODIC)
    assert (after.done, after.runs, after.cursor, after.status) == (
        before.done,
        before.runs,
        before.cursor,
        BatchStatus.COMPLETED,
    )
    assert sum(len(r) for r in _runs(wired.tenant, RunMode.PERIODIC).values()) == runs
    assert [j.id for j in _pages(wired.tenant) if j.payload] == [j.id for j in pages]


@pytest.mark.usefixtures("two_per_page")
def test_a_counterparty_walk_resumes_from_its_cursor_after_a_crash(
    wired: Wired, monkeypatch: pytest.MonkeyPatch
):
    _lists_ready(wired)
    counterparties = sorted(
        _counterparties(wired.tenant, NAMES["U"], NAMES["A"], NAMES["C"], NAMES["R"])
    )
    real = screen_counterparty
    calls = 0

    def crash_on_the_third(
        s: Session,
        row: Counterparty,
        *,
        mode: RunMode,
        trigger: Trigger,
        **options: Unpack[ScreenOptions],
    ) -> list[RunResult]:
        nonlocal calls
        calls += 1
        if calls == 3:
            msg = "worker died"
            raise RuntimeError(msg)
        return real(s, row, mode=mode, trigger=trigger, **options)

    monkeypatch.setattr(walkers, "screen_counterparty", crash_on_the_third)

    _periodic(wired)
    _ = _drain(wired)
    # The second counterparty page failed on its first row and waits for its retry.
    batch = _batch(wired.tenant, BatchKind.PERIODIC)
    assert (batch.status, batch.done, batch.cursor) == (
        BatchStatus.RUNNING,
        2,
        f"cp:{counterparties[1]}",
    )
    with job_session(wired.tenant) as s:
        _ = s.execute(
            update(JobRow)
            .where(JobRow.status == JobStatus.PENDING)
            .values(run_after=clock.now() - timedelta(minutes=1))
        )
    _ = _drain(wired)

    batch = _batch(wired.tenant, BatchKind.PERIODIC)
    assert (batch.status, batch.done, batch.runs) == (BatchStatus.COMPLETED, 4, 4)
    runs = _runs(wired.tenant, RunMode.PERIODIC)
    assert {cp: len(each) for cp, each in runs.items()} == dict.fromkeys(
        counterparties, 1
    )


@pytest.mark.usefixtures("two_per_page")
def test_a_failed_counterparty_walk_resumes_at_its_counterparty_cursor(
    wired: Wired, monkeypatch: pytest.MonkeyPatch
):
    _lists_ready(wired)
    counterparties = sorted(
        _counterparties(wired.tenant, NAMES["U"], NAMES["A"], NAMES["C"])
    )
    cli = Trigger(TriggerType.CLI, "cli:tester")
    with job_session(wired.tenant) as s:
        batch_id = walkers.start_rescreen(
            s, wired.queue, kind=BatchKind.PERIODIC, trigger=cli
        )
    # The party page, then the first counterparty page.
    assert wired.runner.run_once(limit=2) == 2

    def broken(*_args: object, **_kwargs: object) -> None:
        msg = "screening is down"
        raise RuntimeError(msg)

    monkeypatch.setattr(walkers, "screen_counterparty", broken)
    with job_session(wired.tenant) as s:
        _ = s.execute(
            update(JobRow)
            .where(JobRow.status == JobStatus.PENDING)
            .values(max_attempts=1)
        )
    _ = _drain(wired)
    batch = _batch(wired.tenant, BatchKind.PERIODIC)
    assert (batch.id, batch.status, batch.cursor) == (
        batch_id,
        BatchStatus.FAILED,
        f"cp:{counterparties[1]}",
    )
    monkeypatch.undo()
    with job_session(wired.tenant) as s:
        ref = s.get_one(ScreeningBatch, (wired.tenant.tenant_id, batch_id)).ref
        walkers.open_periodic(s, wired.queue, ref=ref)
    _ = _drain(wired)

    batch = _batch(wired.tenant, BatchKind.PERIODIC)
    assert (batch.status, batch.done) == (BatchStatus.COMPLETED, 3)
    last = _pages(wired.tenant)[-1]
    assert last.payload["after_counterparty_id"] == str(counterparties[1])
    runs = _runs(wired.tenant, RunMode.PERIODIC)
    assert {cp: len(each) for cp, each in runs.items()} == dict.fromkeys(
        counterparties, 1
    )


@pytest.mark.usefixtures("two_per_page")
def test_a_failed_first_counterparty_page_reruns_on_reopen(
    wired: Wired, monkeypatch: pytest.MonkeyPatch
):
    _lists_ready(wired)
    (party,) = _parties(wired.tenant, NAMES["U"])
    counterparties = _counterparties(wired.tenant, NAMES["A"], NAMES["C"], NAMES["R"])
    cli = Trigger(TriggerType.CLI, "cli:tester")
    with job_session(wired.tenant) as s:
        batch_id = walkers.start_rescreen(
            s, wired.queue, kind=BatchKind.PERIODIC, trigger=cli
        )
    # The party page only: it enqueues cp:start.
    assert wired.runner.run_once(limit=1) == 1

    def broken(*_args: object, **_kwargs: object) -> None:
        msg = "screening is down"
        raise RuntimeError(msg)

    monkeypatch.setattr(walkers, "screen_counterparty", broken)
    with job_session(wired.tenant) as s:
        _ = s.execute(
            update(JobRow)
            .where(JobRow.status == JobStatus.PENDING)
            .values(max_attempts=1)
        )
    _ = _drain(wired)
    batch = _batch(wired.tenant, BatchKind.PERIODIC)
    assert (batch.id, batch.status, batch.cursor) == (
        batch_id,
        BatchStatus.FAILED,
        str(party),
    )
    assert _runs(wired.tenant, RunMode.PERIODIC) == {}
    monkeypatch.undo()
    with job_session(wired.tenant) as s:
        ref = s.get_one(ScreeningBatch, (wired.tenant.tenant_id, batch_id)).ref
        walkers.open_periodic(s, wired.queue, ref=ref)
    _ = _drain(wired)

    batch = _batch(wired.tenant, BatchKind.PERIODIC)
    assert (batch.id, batch.status, batch.done) == (
        batch_id,
        BatchStatus.COMPLETED,
        4,
    )
    runs = _runs(wired.tenant, RunMode.PERIODIC)
    assert {cp: len(each) for cp, each in runs.items()} == dict.fromkeys(
        counterparties, 1
    )
    assert _party_runs(wired.tenant, RunMode.PERIODIC) == {party}


def test_the_manual_rescreen_covers_counterparties_with_the_cli_trigger(
    wired: Wired,
):
    _lists_ready(wired)
    (party,) = _parties(wired.tenant, NAMES["U"])
    counterparties = set(_counterparties(wired.tenant, NAMES["A"], NAMES["C"]))
    cli = Trigger(TriggerType.CLI, "cli:tester")
    with job_session(wired.tenant) as s:
        batch_id = walkers.start_rescreen(
            s, wired.queue, kind=BatchKind.PERIODIC, trigger=cli
        )
    _ = _drain(wired)

    batch = _batch(wired.tenant, BatchKind.PERIODIC)
    assert (batch.id, batch.status, batch.total, batch.runs) == (
        batch_id,
        BatchStatus.COMPLETED,
        3,
        3,
    )
    runs = _runs(wired.tenant, RunMode.PERIODIC)
    assert set(runs) == counterparties
    assert {
        (r.trigger_type, r.trigger_ref) for each in runs.values() for r in each
    } == {(TriggerType.CLI, "cli:tester")}
    assert _party_runs(wired.tenant, RunMode.PERIODIC) == {party}
    pages = _pages(wired.tenant, batch.id)
    assert len(pages) == 2
    assert all(j.payload["trigger_ref"] == "cli:tester" for j in pages)
