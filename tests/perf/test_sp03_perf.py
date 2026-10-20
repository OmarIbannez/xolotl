"""Screening and re-screening measurements (SP-03 spec §8.3, §10, §15; plan 3c F7).

Marked ``perf``: the default run (and so ``make ci``) deselects them, and
``make perf`` runs them by hand. Each asserts its functional outcome and the
spec's budget, and appends its numbers to ``.perf/sp03.json``. SP-11 gates them
under load; here they are recorded.
"""

import dataclasses
import statistics
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from tests.helpers import staff_session, system_ctx
from tests.modules.screening.catalog.support import load_shared, seed_sources
from tests.modules.screening.review.support import assign_itf
from tests.perf.conftest import SEED, Name, draw_name, record, split_pools
from tests.platform.jobs_support import enqueue
from xolotl.modules.party import service
from xolotl.modules.party.enums import PartyType
from xolotl.modules.party.schemas import PartyCreateRequest, PersonInput
from xolotl.modules.screening.catalog.models import ListVersion
from xolotl.modules.screening.catalog.subscriptions import (
    catch_up,
    reconcile_subscriptions,
)
from xolotl.modules.screening.composition import register_screening
from xolotl.modules.screening.engine.enums import (
    BatchKind,
    BatchStatus,
    RunMode,
    TriggerType,
)
from xolotl.modules.screening.engine.holder import get_index_holder, set_index_holder
from xolotl.modules.screening.engine.models import (
    ScreeningBatch,
    ScreeningMatch,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.engine.names import index_party_names
from xolotl.modules.screening.engine.queries import AdHocSubjectInput
from xolotl.modules.screening.engine.screen import Trigger, screen
from xolotl.modules.screening.engine.subjects import create_ad_hoc_subject
from xolotl.modules.screening.events import LISTS_VERSION_PUBLISHED
from xolotl.modules.screening.lists import (
    EntityType,
    EntryName,
    ListEntry,
    NameQuality,
)
from xolotl.modules.screening.matching import BlockingPlan
from xolotl.modules.screening.matching.evaluation import (
    PERTURBATIONS,
    Draw,
    generate_pairs,
    perturb,
)
from xolotl.modules.screening.rescreen import register_rescreen
from xolotl.modules.screening.rescreen.blocking import (
    BLOCKING_CAP,
    TRIGRAM_THRESHOLD,
    blocking_query,
    name_plans,
    stop_keys,
)
from xolotl.modules.screening.rescreen.list_delta import LIST_DELTA_KIND
from xolotl.modules.screening.rescreen.walkers import PERIODIC_RESCREEN_KIND
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.events import OutboxEventBus
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.jobs import JobRunner, PostgresJobQueue
from xolotl.platform.storage.local import LocalFsObjectStore

pytestmark = pytest.mark.perf

OFAC = "OFAC_SDN"
CONSOLE = Trigger(TriggerType.CONSOLE, "perf")
# NFR-SC-001: one name against the shared shard, p95 well under 300 ms.
FORWARD_ENTRIES = 50_000
WARM_UP = 20
MEASURED = 500
P95_BUDGET_SECONDS = 0.3
# NFR-SC-003, NFR-SC-004: a 10,000-party base; 5 % of it near the delta's entries.
BASE_PARTIES = 10_000
DELTA_ADDED = 50
DELTA_CHANGED = 50
NEAR_PER_ENTRY = 5  # 100 delta entries x 5 = 500 parties, 5 % of the base
FILLER_ENTRIES = 1_000
DELTA_BUDGET_SECONDS = 120.0
# Background parties share no token with the lists; fuzzy scoring may band a few.
BACKGROUND_SHARE = 0.01
PERIODIC_FLOOR_PER_SECOND = 20.0
CREATE_CHUNK = 500
# Makes the list events due at once; the parties' events are left pending.
AGE_EVENTS = """
UPDATE outbox SET created_at = created_at - interval '1 minute' WHERE type = :type
"""


@dataclass(frozen=True, slots=True)
class Wired:
    tenant: CreatedTenant
    bus: OutboxEventBus
    queue: PostgresJobQueue
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
        tenant, OutboxEventBus(factory), queue, LocalFsObjectStore(tmp_path / "objs")
    )
    reset_subscribers()
    set_index_holder(None)


@contextmanager
def _job(tenant: CreatedTenant) -> Generator[Session]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        yield s


def _mlro(tenant: CreatedTenant):
    return staff_session(tenant.tenant_id, tenant.admin_user_id, roles=(Role.MLRO,))


def _entry(key: str, name: str) -> ListEntry:
    return ListEntry(
        list_code=OFAC,
        entry_key=key,
        entity_type=EntityType.PERSON,
        primary_name=EntryName(name.upper(), NameQuality.PRIMARY),
        source_ref=f"perf {key}",
    )


def _drain(runner: JobRunner) -> int:
    ran = 0
    while step := runner.run_once():
        ran += step
    return ran


def _p(timings: list[float], n: int, at: int) -> float:
    return statistics.quantiles(timings, n=n)[at - 1] * 1000


# ---- forward screening ---------------------------------------------------------------


def _forward_entries(rng: Draw) -> list[ListEntry]:
    """Draw 50,000 names from the full tables, each through one library perturbation.

    Returns:
        The entries, keyed ``S00000`` onwards.

    """
    (party_given, party_family), (list_given, list_family) = split_pools()
    given = tuple(sorted(party_given + list_given))
    family = tuple(sorted(party_family + list_family))
    kinds = list(PERTURBATIONS)
    entries: list[ListEntry] = []
    for i in range(FORWARD_ENTRIES):
        base = " ".join(draw_name(rng, given, family))
        entries.append(_entry(f"S{i:05d}", perturb(base, rng.sample(kinds, 1), rng)))
    return entries


def _forward_queries(rng: Draw, entries: list[ListEntry]) -> list[str]:
    """Half the queries perturb a listed name (library generator), half are fresh.

    Returns:
        ``WARM_UP + MEASURED`` names, shuffled.

    """
    total = WARM_UP + MEASURED
    sample = rng.sample(entries, total // 2)
    near = [p.query.names[0] for p in generate_pairs(sample, per_entry=1, seed=SEED)]
    (given, family), _ = split_pools()
    fresh = [" ".join(draw_name(rng, given, family)) for _ in range(total - len(near))]
    return rng.sample(near + fresh, total)


def _screen_ad_hoc(tenant: CreatedTenant, name: str) -> tuple[float, float]:
    """Screen one name as a console check does.

    Returns:
        The seconds spent in ``screen()`` and in the whole transaction.

    """
    began = time.perf_counter()
    with _mlro(tenant) as s:
        subject = create_ad_hoc_subject(
            s, AdHocSubjectInput(type="PERSON", names=[name])
        )
        start = time.perf_counter()
        _ = screen(s, subject, mode=RunMode.AD_HOC, trigger=CONSOLE)
        screened = time.perf_counter() - start
    return screened, time.perf_counter() - began


def test_forward_screen_p95_at_50k_entries(wired: Wired):
    rng = Draw(SEED)
    entries = _forward_entries(rng)
    started = time.perf_counter()
    report = load_shared(OFAC, entries, store=wired.store)
    ingest_seconds = time.perf_counter() - started
    assert report.entry_count == FORWARD_ENTRIES
    started = time.perf_counter()
    holder = get_index_holder()
    holder.warm()
    with _mlro(wired.tenant) as s:
        assert holder.shared(s).versions[OFAC].version_id == report.version_id
    warm_seconds = time.perf_counter() - started

    queries = _forward_queries(rng, entries)
    for name in queries[:WARM_UP]:
        _ = _screen_ad_hoc(wired.tenant, name)
    measured = [_screen_ad_hoc(wired.tenant, name) for name in queries[WARM_UP:]]
    timings = [screened for screened, _ in measured]
    totals = [total for _, total in measured]

    with _job(wired.tenant) as s:
        runs = s.scalar(select(func.count()).select_from(ScreeningRun))
    p95 = _p(timings, 20, 19)
    record(
        "forward_screen_p95_at_50k_entries",
        entries=FORWARD_ENTRIES,
        screens=MEASURED,
        ingest_s=round(ingest_seconds, 2),
        index_load_s=round(warm_seconds, 2),
        p50_ms=round(statistics.median(timings) * 1000, 2),
        p95_ms=round(p95, 2),
        p99_ms=round(_p(timings, 100, 99), 2),
        transaction_p95_ms=round(_p(totals, 20, 19), 2),
    )
    assert runs == WARM_UP + MEASURED
    assert p95 < P95_BUDGET_SECONDS * 1000, f"p95 {p95:.1f} ms"


# ---- the 10,000-party base -----------------------------------------------------------


def _person(name: Name) -> PartyCreateRequest:
    given, first, second = name
    return PartyCreateRequest(
        party_type=PartyType.PERSON,
        person=PersonInput(
            given_names=given, first_surname=first, second_surname=second
        ),
    )


def _add_indexed(tenant: CreatedTenant, names: list[Name]) -> list[UUID]:
    """Create the parties and index their names, ``CREATE_CHUNK`` per transaction.

    The party events stay in the outbox: indexing here replaces their subscriber.

    Returns:
        The party ids, in ``names`` order.

    """
    ids: list[UUID] = []
    for at in range(0, len(names), CREATE_CHUNK):
        with _mlro(tenant) as s:
            for name in names[at : at + CREATE_CHUNK]:
                party_id = service.create_party(s, _person(name)).id
                _ = index_party_names(s, party_id)
                ids.append(party_id)
    return ids


def _unique_names(
    rng: Draw, pool: tuple[tuple[str, ...], tuple[str, ...]], count: int
) -> list[Name]:
    seen: set[Name] = set()
    names: list[Name] = []
    while len(names) < count:
        name = draw_name(rng, *pool)
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


@dataclass(frozen=True, slots=True)
class PerfBase:
    """The base and the list versions the re-screen tests share."""

    v1: list[ListEntry]
    v2: list[ListEntry]
    near: dict[UUID, str]  # party id -> the delta entry it carries the name of
    party_ids: list[UUID]
    build_seconds: float


def _build_base() -> tuple[list[ListEntry], list[ListEntry], list[Name], list[Name]]:
    """Draw the lists and the base: disjoint name pools, so only near parties match.

    Returns:
        v1, v2, the background names, and the delta entries' names in entry order.

    """
    rng = Draw(SEED)
    party_pool, list_pool = split_pools()
    delta_count = DELTA_ADDED + DELTA_CHANGED
    listed = _unique_names(rng, list_pool, FILLER_ENTRIES + delta_count)
    filler = [
        _entry(f"F{i:04d}", " ".join(n)) for i, n in enumerate(listed[:FILLER_ENTRIES])
    ]
    delta_names = listed[FILLER_ENTRIES:]
    changed = [
        _entry(f"C{i:03d}", " ".join(n))
        for i, n in enumerate(delta_names[:DELTA_CHANGED])
    ]
    added = [
        _entry(f"A{i:03d}", " ".join(n))
        for i, n in enumerate(delta_names[DELTA_CHANGED:])
    ]
    v1 = [*filler, *changed]
    v2 = [
        *filler,
        *(dataclasses.replace(e, programs=("SDGT",)) for e in changed),
        *added,
    ]
    background = _unique_names(
        rng, party_pool, BASE_PARTIES - delta_count * NEAR_PER_ENTRY
    )
    return v1, v2, background, delta_names


@pytest.fixture
def perf_base(wired: Wired) -> PerfBase:
    v1, v2, background, delta_names = _build_base()
    delta_keys = [
        *(f"C{i:03d}" for i in range(DELTA_CHANGED)),
        *(f"A{i:03d}" for i in range(DELTA_ADDED)),
    ]
    near_names = [n for n in delta_names for _ in range(NEAR_PER_ENTRY)]
    near_keys = [k for k in delta_keys for _ in range(NEAR_PER_ENTRY)]
    started = time.perf_counter()
    near_ids = _add_indexed(wired.tenant, near_names)
    background_ids = _add_indexed(wired.tenant, background)
    build_seconds = time.perf_counter() - started
    return PerfBase(
        v1,
        v2,
        dict(zip(near_ids, near_keys, strict=True)),
        near_ids + background_ids,
        build_seconds,
    )


def _hand_over(wired: Wired, entries: list[ListEntry]) -> UUID:
    """Load a version and hand it over as ``lists.poll`` does.

    Only the list events are dispatched: the parties' events stay in the outbox.

    Returns:
        The version's id.

    """
    version_id = load_shared(OFAC, entries, store=wired.store).version_id
    assert version_id is not None
    with _job(wired.tenant) as s:
        _ = reconcile_subscriptions(s, today=date(2026, 10, 19))
        _ = catch_up(s)
        _ = s.execute(
            text(AGE_EVENTS),
            {"type": LISTS_VERSION_PUBLISHED},
        )
    while wired.bus.dispatch_pending():
        pass
    return version_id


def _batch(tenant: CreatedTenant, kind: BatchKind) -> ScreeningBatch:
    with _job(tenant) as s:
        return s.scalars(
            select(ScreeningBatch).where(ScreeningBatch.kind == kind)
        ).one()


def _findings(tenant: CreatedTenant) -> list[tuple[UUID, str]]:
    with _job(tenant) as s:
        rows = s.execute(
            select(ScreeningSubject.party_id, ScreeningMatch.source_entry_key).join(
                ScreeningMatch, ScreeningMatch.subject_id == ScreeningSubject.id
            )
        ).all()
    found: list[tuple[UUID, str]] = []
    for party_id, key in rows:
        assert party_id is not None
        found.append((party_id, key))
    return found


def _runs_per_party(tenant: CreatedTenant, mode: RunMode) -> dict[UUID, int]:
    with _job(tenant) as s:
        rows = s.execute(
            select(ScreeningSubject.party_id, func.count())
            .join(ScreeningRun, ScreeningRun.subject_id == ScreeningSubject.id)
            .where(ScreeningRun.mode == mode)
            .group_by(ScreeningSubject.party_id)
        ).all()
    counts: dict[UUID, int] = {}
    for party_id, count in rows:
        assert party_id is not None
        counts[party_id] = count
    return counts


# ---- delta and periodic re-screens ---------------------------------------------------


def _overflowed(caplog: pytest.LogCaptureFixture) -> set[str]:
    return {
        str(vars(r)["entry_key"])  # the fields the job attached to the record
        for r in caplog.records
        if r.getMessage() == "screening.delta_entry_overflow"
    }


@dataclass(frozen=True, slots=True)
class Blocked:
    """Each delta entry's blocked parties, without ``BLOCKING_CAP``."""

    per_entry: dict[str, set[UUID]]
    stop_keys: int

    def counts(self) -> list[int]:
        return sorted(len(ids) for ids in self.per_entry.values())

    def union(self, *, capped: bool) -> int:
        kept = (
            ids
            for ids in self.per_entry.values()
            if not capped or len(ids) <= BLOCKING_CAP
        )
        return len(set[UUID]().union(*kept))


def _blocked_entry(
    s: Session, plans: list[tuple[BlockingPlan, str]], stop: frozenset[str]
) -> set[UUID]:
    found: set[UUID] = set()
    for plan, folded in plans:
        query = blocking_query(plan, folded, limit=BASE_PARTIES + 1, stop=stop)
        found.update(s.scalars(query))
    return found


def _blocked(tenant: CreatedTenant, entries: list[ListEntry], version: UUID) -> Blocked:
    """Block each entry as ``reverse_candidates`` does, but with no cap.

    The same ``blocking_query`` over the version's shard IDF, under a tenant session,
    with the stop keys of the whole delta (one page here), limited only by the
    base's size, so an overflowing entry is counted too.

    Returns:
        The parties each entry's names block, by entry key, and how many stop keys
        the delta had.

    """
    per_entry: dict[str, set[UUID]] = {}
    with _job(tenant) as s:
        seq = s.get_one(ListVersion, version).seq
        idf = get_index_holder().shared(s, require={OFAC: seq}).blocking_idf
        _ = s.execute(
            text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"),
            {"t": str(TRIGRAM_THRESHOLD)},
        )
        plans = {e.entry_key: name_plans(e, idf) for e in entries}
        stop = stop_keys(
            s, (plan for entry_plans in plans.values() for plan, _ in entry_plans)
        )
        for key, entry_plans in plans.items():
            per_entry[key] = _blocked_entry(s, entry_plans, stop)
    return Blocked(per_entry, len(stop))


def test_delta_rescreen_of_a_10k_party_base(
    wired: Wired, perf_base: PerfBase, caplog: pytest.LogCaptureFixture
):
    _ = _hand_over(wired, perf_base.v1)
    # Only the delta pages run: v1's initial walk stays queued.
    runner = JobRunner(
        wired.queue, get_sessionmaker(), worker_id="w-perf", kinds=[LIST_DELTA_KIND]
    )
    delta_keys = set(perf_base.near.values())
    started = time.perf_counter()
    v2 = _hand_over(wired, perf_base.v2)
    handed_over = time.perf_counter() - started
    # Measured off the clock, before the pages run: the parties each entry blocks.
    blocked = _blocked(
        wired.tenant, [e for e in perf_base.v2 if e.entry_key in delta_keys], v2
    )
    started = time.perf_counter()
    pages = _drain(runner)
    elapsed = handed_over + time.perf_counter() - started

    batch = _batch(wired.tenant, BatchKind.LIST_DELTA)
    found = set(_findings(wired.tenant))
    overflowed = _overflowed(caplog)
    background = {(p, k) for p, k in found if p not in perf_base.near}
    counts = blocked.counts()
    record(
        "delta_rescreen_of_a_10k_party_base",
        parties=BASE_PARTIES,
        delta_entries=DELTA_ADDED + DELTA_CHANGED,
        base_build_s=round(perf_base.build_seconds, 2),
        elapsed_s=round(elapsed, 2),
        elapsed_covers="loading and handing over v2, then draining the delta pages",
        pages=pages,
        screened_parties=batch.runs,
        blocked_per_entry_min=counts[0],
        blocked_per_entry_median=statistics.median(counts),
        blocked_per_entry_p90=round(statistics.quantiles(counts, n=10)[8], 1),
        blocked_per_entry_max=counts[-1],
        blocked_union=blocked.union(capped=False),
        blocked_union_under_cap=blocked.union(capped=True),
        stop_keys=blocked.stop_keys,
        overflowed_entries=len(overflowed),
        near_parties_of_overflowed_entries=sum(
            k in overflowed for k in perf_base.near.values()
        ),
        findings=len(found),
        background_findings=len(background),
        last_error=batch.last_error or "",
    )
    assert batch.status is BatchStatus.COMPLETED
    assert batch.done == DELTA_ADDED + DELTA_CHANGED
    # An entry whose blocking union passes BLOCKING_CAP leaves its name candidates
    # to the periodic run (plan 3c, Deviations: Overflow). Every other near party
    # is found on its own entry. The background shares no token with the lists, but
    # fuzzy scoring may still band a few of its names, and only on delta entries.
    expected = {(p, k) for p, k in perf_base.near.items() if k not in overflowed}
    assert batch.last_error == (f"OVERFLOW:{len(overflowed)}" if overflowed else None)
    assert expected <= found
    assert {key for _, key in found} <= delta_keys
    assert len(background) <= BACKGROUND_SHARE * len(found), background
    assert elapsed < DELTA_BUDGET_SECONDS, f"{elapsed:.1f} s"


def test_periodic_rescreen_throughput(wired: Wired, perf_base: PerfBase):
    # The list is loaded without a hand-over, so no initial walk competes.
    _ = load_shared(OFAC, perf_base.v2, store=wired.store)
    runner = JobRunner(
        wired.queue,
        get_sessionmaker(),
        worker_id="w-perf",
        kinds=[PERIODIC_RESCREEN_KIND],
    )
    _ = enqueue(wired.queue, wired.tenant.tenant_id, kind=PERIODIC_RESCREEN_KIND)
    started = time.perf_counter()
    pages = _drain(runner)
    elapsed = time.perf_counter() - started

    batch = _batch(wired.tenant, BatchKind.PERIODIC)
    runs = _runs_per_party(wired.tenant, RunMode.PERIODIC)
    rate = BASE_PARTIES / elapsed
    record(
        "periodic_rescreen_throughput",
        parties=BASE_PARTIES,
        entries=len(perf_base.v2),
        elapsed_s=round(elapsed, 2),
        pages=pages,
        parties_per_s=round(rate, 1),
        findings=len(_findings(wired.tenant)),
    )
    assert batch.status is BatchStatus.COMPLETED
    assert (batch.total, batch.done) == (BASE_PARTIES, BASE_PARTIES)
    assert set(runs) == set(perf_base.party_ids)
    assert set(runs.values()) == {1}
    assert rate >= PERIODIC_FLOOR_PER_SECOND, f"{rate:.1f} parties/s"
