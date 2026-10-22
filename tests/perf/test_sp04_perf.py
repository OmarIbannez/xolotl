"""Decide, alert and counterparty screening measurements (SP-04 spec §12, §8.5, §11).

Marked ``perf``: the default run (and so ``make ci``) deselects them, and
``make perf`` runs them by hand (plan 4c C10). Each asserts its functional outcome
and a generous floor, and appends its numbers to ``.perf/sp04.json``; SP-11 gates
``NFR-TM-001`` and ``NFR-TM-002`` under load.

No test runs the partition job: the decide test creates its partitions before it
ingests anything, and its runner only runs ``monitoring.process`` jobs, so no
partition is created while a decision is measured (C4's note: creating a
``transaction`` partition locks ``txn_decision``, and the other way round).
"""

import statistics
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import platform_ctx, system_ctx
from tests.modules.monitoring.decide.support import (
    TEST_BUDGET_MS,
    DecideStage,
    build_decide_stage,
    mark_sync,
)
from tests.modules.monitoring.rules.hook_support import (
    CASH,
    Stage,
    add_relationship,
    build_stage,
    ingest,
    local,
)
from tests.modules.monitoring.support import make_item
from tests.modules.party.route_support import machine_headers
from tests.modules.screening.catalog.support import load_shared, seed_sources
from tests.modules.screening.review.support import assign_itf
from tests.perf.conftest import SEED, SP04_FILE, Name, draw_name, record, split_pools
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.decide import service as decide_service
from xolotl.modules.monitoring.decide.enums import DecisionMode
from xolotl.modules.monitoring.decide.service import DecisionResult, decide
from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.kinds import PROCESS_KIND
from xolotl.modules.monitoring.process.hooks import reset_processing_hooks
from xolotl.modules.monitoring.rules.assignments import clear_rule_cache
from xolotl.modules.monitoring.rules.evaluate import profile_for
from xolotl.modules.monitoring.rules.models import RuleHit
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import (
    EnrichedTransaction,
    TransactionInput,
    parse_item,
)
from xolotl.modules.screening.composition import register_screening
from xolotl.modules.screening.counterparty.first_sight import FIRST_SIGHT_KIND
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.resolve import resolve
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyInput,
    CounterpartyKind,
    CounterpartyScreeningStatus,
)
from xolotl.modules.screening.engine.holder import get_index_holder, set_index_holder
from xolotl.modules.screening.lists import EntityType, EntryName, ListEntry, NameQuality
from xolotl.modules.screening.matching.evaluation import Draw
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import Confidence
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.jobs import JobRunner, PostgresJobQueue
from xolotl.platform.partitions import ensure_partitions
from xolotl.platform.storage.local import LocalFsObjectStore
from xolotl.platform.units import UnitValue, upsert_value
from xolotl.worker import Worker

pytestmark = pytest.mark.perf

SATURDAY = 5


def _drain(runner: JobRunner) -> int:
    ran = 0
    while step := runner.run_once(limit=100):
        ran += step
    return ran


def _process_runner(stage: Stage, worker: str) -> JobRunner:
    return JobRunner(
        stage.services.queue, get_sessionmaker(), worker_id=worker, kinds=[PROCESS_KIND]
    )


def _quantile(values: list[float], n: int, at: int) -> float:
    # Inclusive: a quantile of a few samples (ten hits) stays within them.
    return statistics.quantiles(values, n=n, method="inclusive")[at - 1]


def _summary(prefix: str, values_ms: list[float]) -> dict[str, float]:
    return {
        f"{prefix}_p50_ms": round(statistics.median(values_ms), 2),
        f"{prefix}_p95_ms": round(_quantile(values_ms, 20, 19), 2),
        f"{prefix}_p99_ms": round(_quantile(values_ms, 100, 99), 2),
        f"{prefix}_max_ms": round(max(values_ms), 2),
    }


def _local_instant(day: date, hour: int, minute: int) -> datetime:
    return datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=MEXICO_CITY_ZONE
    ).astimezone(UTC)


# ---- decide latency (NFR-TM-001, FR-TM-003) -----------------------------------------

DECIDE_RELATIONSHIPS = 200
HISTORY_PER_RELATIONSHIP = 60  # 12,000 processed transactions
HISTORY_START = date(2026, 6, 14)
HISTORY_DAYS = 183  # 14 June to 13 December 2026: six months before the decisions
DECIDE_AT = local(14, 12)  # Monday 14 December 2026, 12:00 Mexico City
WARM_UP = 50
MEASURED = 1_000
COLD_AFTER_CACHE = 10
# Ten years of monthly partitions, January 2017 to January 2027 (spec §5.2; C5's
# review note: about 121 at ten years of retention).
PARTITIONS_FROM = date(2017, 1, 1)
PARTITION_MONTHS = 121
DECIDE_FLOOR_MS = 500.0  # generous for a laptop; SP-11 gates p99 <= 100 ms
SERVICE_BUDGET_MS = 80
NFR_P99_MS = 100
TRUE_MATCH_EVERY = 20


def _load_june_values(admin_engine: Engine) -> int:
    """Load fixture unit values for June 2026, which B7's file starts after.

    UDI every day from 31 May (the last day before June) and the FIX on weekdays,
    so a June transaction resolves every unit; fixture values, not published ones.

    Returns:
        How many values were loaded.

    """
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    fetched = datetime(2026, 6, 30, 12, tzinfo=UTC)
    loaded = 0
    with tenant_session(platform_ctx(), factory=owner) as s:
        day = date(2026, 5, 31)
        while day <= date(2026, 6, 30):
            rows = [("UDI", Decimal("8.400000") + Decimal(day.day) / 1000)]
            if day.weekday() < SATURDAY:
                rows.append(("USD_MXN_FIX", Decimal("18.1500")))
            for code, value in rows:
                _ = upsert_value(
                    s,
                    UnitValue(
                        unit_code=code,
                        valid_from=day,
                        valid_to=day,
                        value=value,
                        quote_ccy="MXN",
                        source="fixture",
                        confidence=Confidence.VERIFIED,
                    ),
                    fetched_at=fetched,
                )
                loaded += 1
            day += timedelta(days=1)
    return loaded


_PARTITIONS_SQL = text(
    "SELECT count(*) FROM pg_inherits WHERE inhparent = CAST(:p AS regclass)"
)


def _transaction_partitions(admin_engine: Engine) -> int:
    with admin_engine.connect() as conn:
        count = conn.scalar(
            _PARTITIONS_SQL,
            {"p": "transaction"},
        )
    return int(count or 0)


@dataclass(frozen=True, slots=True)
class Customer:
    external_ref: str
    counterparty: dict[str, object]


def _customers(tenant: CreatedTenant) -> list[Customer]:
    """Add 200 relationships, half N1 and half N2, each with its own counterparty.

    Returns:
        The customers, in creation order.

    """
    rng = Draw(SEED)
    pool, _ = split_pools()
    customers: list[Customer] = []
    for index in range(DECIDE_RELATIONSHIPS):
        ref = f"perf-{index:03d}"
        _ = add_relationship(tenant, ref, "N1" if index % 2 == 0 else "N2")
        counterparty: dict[str, object] = {
            "kind": "PERSON",
            "name": " ".join(draw_name(rng, *pool)),
            "account": f"0121800{index:011d}",
        }
        customers.append(Customer(ref, counterparty))
    return customers


def _history(customers: list[Customer]) -> dict[date, list[dict[str, object]]]:
    """Draw each customer's 60 operations over the six months, grouped by day.

    A third are SPEI transfers in from the customer's counterparty, the rest cash
    deposits; amounts of 50 to 400 pesos keep a month under the N1 limit.

    Returns:
        The items of each local day, in day order.

    """
    rng = Draw(SEED + 1)
    by_day: dict[date, list[dict[str, object]]] = {}
    for index, customer in enumerate(customers):
        for number in range(HISTORY_PER_RELATIONSHIP):
            offset = (number * HISTORY_DAYS) // HISTORY_PER_RELATIONSHIP + index % 3
            day = HISTORY_START + timedelta(days=min(offset, HISTORY_DAYS - 1))
            occurred = _local_instant(day, rng.randrange(8, 20), rng.below(60))
            item = make_item(
                external_id=f"h-{index:03d}-{number:02d}",
                occurred_at=occurred.isoformat(),
                customer_ref=customer.external_ref,
                amount=f"{rng.randrange(50, 400)}.00",
                currency="MXN",
            )
            if number % 3 == 0:
                item.update(
                    rail="SPEI",
                    instrument="TRANSFER",
                    channel="APP",
                    counterparty=customer.counterparty,
                )
            by_day.setdefault(day, []).append(item)
    return dict(sorted(by_day.items()))


def _screen_counterparties(admin_engine: Engine, tenant_id: UUID) -> int:
    """Cache a status on every counterparty, as the first-sight job would have.

    Every twentieth is ``TRUE_MATCH`` (which the sync ``COUNTERPARTY_TRUE_MATCH``
    blocks on), the rest ``CLEAR``.

    Returns:
        How many are ``TRUE_MATCH``.

    """
    with admin_engine.begin() as conn:
        ids = list(
            conn.scalars(
                select(Counterparty.id)
                .where(Counterparty.tenant_id == tenant_id)
                .order_by(Counterparty.id)
            )
        )
        flagged = ids[::TRUE_MATCH_EVERY]
        _ = conn.execute(
            update(Counterparty)
            .where(Counterparty.tenant_id == tenant_id)
            .values(
                screening_status=CounterpartyScreeningStatus.CLEAR.value,
                screened_at=func.now(),
            )
        )
        _ = conn.execute(
            update(Counterparty)
            .where(Counterparty.tenant_id == tenant_id, Counterparty.id.in_(flagged))
            .values(screening_status=CounterpartyScreeningStatus.TRUE_MATCH.value)
        )
    return len(flagged)


@dataclass(frozen=True, slots=True)
class DecideBase:
    stage: DecideStage
    customers: list[Customer]
    partitions: int
    history_rows: int
    true_match_counterparties: int
    setup_s: float


@pytest.fixture
def decide_base(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[DecideBase]:
    started = time.perf_counter()
    with admin_engine.begin() as conn:
        _ = ensure_partitions(
            conn,
            table="transaction",
            start=PARTITIONS_FROM,
            months_ahead=PARTITION_MONTHS - 1,
        )
    _ = _load_june_values(admin_engine)
    stage = build_decide_stage(tenant, admin_engine, monkeypatch)
    mark_sync(admin_engine, stage)
    customers = _customers(tenant)
    runner = _process_runner(stage.stage, "w-perf-history")
    rows = 0
    for items in _history(customers).values():
        rows += len(ingest(stage.stage, items))
        _ = _drain(runner)
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        statuses = set(
            s.scalars(
                select(Transaction.status)
                .where(Transaction.external_id.like("h-%"))
                .distinct()
            )
        )
    # A parked month would thin the aggregates and flatter the decide numbers.
    assert statuses == {TransactionStatus.PROCESSED}, statuses
    flagged = _screen_counterparties(admin_engine, stage.tenant_id)
    monkeypatch.setattr(get_settings(), "monitoring_decide_budget_ms", TEST_BUDGET_MS)
    yield DecideBase(
        stage,
        customers,
        _transaction_partitions(admin_engine),
        rows,
        flagged,
        time.perf_counter() - started,
    )
    reset_processing_hooks()


def _candidates(customers: list[Customer], count: int) -> list[TransactionInput]:
    """Draw ``count`` cash deposits over the customers, half naming a counterparty.

    Returns:
        The validated items, in a seeded order.

    """
    rng = Draw(SEED + 2)
    occurred = DECIDE_AT - timedelta(minutes=5)
    items: list[TransactionInput] = []
    for number in range(count):
        customer = rng.choice(customers)
        body = make_item(
            external_id=f"d-{number:04d}",
            occurred_at=occurred.isoformat(),
            customer_ref=customer.external_ref,
            amount=f"{rng.randrange(100, 3000)}.00",
            currency="MXN",
        )
        if number % 2 == 0:
            body["counterparty"] = customer.counterparty
        parsed = parse_item(
            number,
            body,
            now=DECIDE_AT,
            max_future_minutes=get_settings().monitoring_max_future_minutes,
        )
        assert not isinstance(parsed, list), parsed
        items.append(parsed)
    return items


@dataclass(slots=True)
class ProfileTimer:
    """Wraps the decide path's ``profile_for`` and keeps each call's seconds."""

    calls: list[float]

    def wrap(self) -> Callable[[Session, EnrichedTransaction], object]:
        def timed(s: Session, txn: EnrichedTransaction) -> object:
            began = time.perf_counter()
            try:
                return profile_for(s, txn)
            finally:
                self.calls.append(time.perf_counter() - began)

        return timed


@dataclass(frozen=True, slots=True)
class Timed:
    result: DecisionResult
    transaction_ms: float
    db_ms: int


def _decide(tenant_id: UUID, item: TransactionInput) -> Timed:
    """Decide in a transaction of its own, as the route does, and commit.

    Returns:
        The result, the whole transaction's milliseconds and the row's ``db_ms``.

    """
    began = time.perf_counter()
    with tenant_session(system_ctx(tenant_id)) as s:
        result = decide(s, item, actor="api_client:perf")
    elapsed = (time.perf_counter() - began) * 1000
    with tenant_session(system_ctx(tenant_id)) as s:
        db_ms = s.scalar(
            text("SELECT db_ms FROM txn_decision WHERE id = :id"),
            {"id": result.decision_id},
        )
    return Timed(result, elapsed, int(db_ms or 0))


def test_decide_p99_under_the_budget(
    decide_base: DecideBase, monkeypatch: pytest.MonkeyPatch
):
    tenant_id = decide_base.stage.tenant_id
    profile = ProfileTimer([])
    monkeypatch.setattr(decide_service, "profile_for", profile.wrap())
    monkeypatch.setattr(clock, "now", lambda: DECIDE_AT)
    items = _candidates(
        decide_base.customers, 1 + WARM_UP + MEASURED + COLD_AFTER_CACHE
    )

    # The first decision of this process, its rule set not cached (the history's
    # processing cached it, so it is forgotten first).
    clear_rule_cache()
    cold_first = _decide(tenant_id, items[0])
    for item in items[1 : 1 + WARM_UP]:
        _ = _decide(tenant_id, item)
    profile.calls.clear()
    measured = [
        _decide(tenant_id, item) for item in items[1 + WARM_UP : 1 + WARM_UP + MEASURED]
    ]
    profile_ms = [seconds * 1000 for seconds in profile.calls]
    # Each after the rule-set cache expired, as after
    # XOLOTL_MONITORING_RULE_CACHE_SECONDS.
    cold_after_cache: list[Timed] = []
    for item in items[1 + WARM_UP + MEASURED :]:
        clear_rule_cache()
        cold_after_cache.append(_decide(tenant_id, item))

    latencies = [float(t.result.latency_ms) for t in measured]
    transactions = [t.transaction_ms for t in measured]
    db = [float(t.db_ms) for t in measured]
    counts = {
        outcome.value: sum(t.result.decision is outcome for t in measured)
        for outcome in {t.result.decision for t in measured}
    }
    p99 = _quantile(latencies, 100, 99)
    cold_ms = [float(t.result.latency_ms) for t in cold_after_cache]
    record(
        "decide_p99_under_the_budget",
        SP04_FILE,
        relationships=DECIDE_RELATIONSHIPS,
        history_transactions=decide_base.history_rows,
        transaction_partitions=decide_base.partitions,
        setup_s=round(decide_base.setup_s, 1),
        decisions=MEASURED,
        warm_up=WARM_UP,
        with_counterparty=sum(
            item.counterparty is not None
            for item in items[1 + WARM_UP : 1 + WARM_UP + MEASURED]
        ),
        true_match_counterparties=decide_base.true_match_counterparties,
        measured_budget_ms=TEST_BUDGET_MS,
        service_budget_ms=SERVICE_BUDGET_MS,
        nfr_p99_ms=NFR_P99_MS,
        over_service_budget=sum(v > SERVICE_BUDGET_MS for v in latencies),
        **_summary("latency", latencies),
        **_summary("transaction", transactions),
        db_p95_ms=round(_quantile(db, 20, 19), 2),
        **_summary("profile_for", profile_ms),
        profile_for_share=round(sum(profile_ms) / sum(latencies), 3),
        cold_first_ms=cold_first.result.latency_ms,
        cold_first_transaction_ms=round(cold_first.transaction_ms, 2),
        cold_after_cache_p50_ms=round(statistics.median(cold_ms), 2),
        cold_after_cache_max_ms=round(max(cold_ms), 2),
        outcomes=", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
    )
    every = [cold_first, *measured, *cold_after_cache]
    assert {t.result.mode for t in every} == {DecisionMode.EVALUATED}
    assert len(profile_ms) == MEASURED
    assert p99 < DECIDE_FLOOR_MS, f"p99 {p99:.1f} ms"


# ---- alert latency (NFR-TM-002) -----------------------------------------------------

ALERT_RELATIONSHIPS = 100
ALERT_TRANSACTIONS = 1_000
STRUCTURING_SCENARIOS = 10
STRUCTURING_USD = ("3600.00", "3700.00", "3800.00")
# The batch arrives at Monday 14 December 2026, 12:00 Mexico City, and its
# operations happened in the 23 hours before, so none is late (P1D).
ALERT_AT = local(14, 12)
ALERT_SPAN_MINUTES = 23 * 60
DRAIN_FLOOR_S = 300.0
NFR_ALERT_P95_S = 5.0
DRAIN_POLL_S = 0.1
STOP_WAIT_S = 30.0


def _pending_rows(tenant_id: UUID) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return (
            s.scalar(
                select(func.count())
                .select_from(Transaction)
                .where(
                    Transaction.external_id.like("a-%"),
                    Transaction.status != TransactionStatus.PROCESSED,
                )
            )
            or 0
        )


@dataclass(slots=True)
class LiveWorker:
    """The MVP's worker loop on a thread of its own, as the API embeds it.

    It ticks the outbox dispatcher, the job runner (``run_once()`` with its default
    limit) and the scheduler, then pauses ``WORKER_TICK_SECONDS``, exactly as
    ``xolotl.worker.Worker.run`` does in production.
    """

    worker: Worker
    ticks: list[int]
    thread: threading.Thread

    @classmethod
    def start(cls, stage: Stage) -> "LiveWorker":
        services = stage.services
        worker = Worker(
            bus=services.bus,
            runner=services.job_runner,
            scheduler=services.scheduler,
            tick_seconds=get_settings().worker_tick_seconds,
        )
        ticks: list[int] = []
        thread = threading.Thread(target=lambda: ticks.append(worker.run()))
        thread.start()
        return cls(worker, ticks, thread)

    def stop(self) -> int:
        self.worker.stop()
        self.thread.join(STOP_WAIT_S)
        return self.ticks[0] if self.ticks else -1


def _wait_processed(tenant_id: UUID, *, began: float) -> None:
    pause = threading.Event()
    while _pending_rows(tenant_id) and time.perf_counter() - began < DRAIN_FLOOR_S:
        _ = pause.wait(DRAIN_POLL_S)


def _alert_items(refs: list[str]) -> list[dict[str, object]]:
    """Ten operations per customer; ten seeded customers structure cash.

    The planted customers are drawn at random, so their processing jobs fall
    anywhere in the queue (jobs run in party id order, and the customers are
    created in index order), not always first.

    A planted customer's three deposits fall in ``[3,500, 5,000)`` USD inside the
    rule's seven days; every other operation is a cash deposit of 100 to 5,000
    pesos, far under that band.

    Returns:
        The batch's items, in a seeded order.

    """
    rng = Draw(SEED + 3)
    structuring = set(rng.sample(range(len(refs)), STRUCTURING_SCENARIOS))
    per = ALERT_TRANSACTIONS // ALERT_RELATIONSHIPS
    items: list[dict[str, object]] = []
    for index, ref in enumerate(refs):
        for number in range(per):
            minutes = rng.randrange(1, ALERT_SPAN_MINUTES)
            occurred = ALERT_AT - timedelta(minutes=minutes)
            planted = index in structuring and number < len(STRUCTURING_USD)
            items.append(
                make_item(
                    external_id=f"a-{index:03d}-{number:02d}",
                    occurred_at=occurred.isoformat(),
                    customer_ref=ref,
                    amount=(
                        STRUCTURING_USD[number]
                        if planted
                        else f"{rng.randrange(100, 5000)}.00"
                    ),
                    currency="USD" if planted else "MXN",
                )
            )
    return rng.sample(items, len(items))


def _db_skew(engine: Engine) -> timedelta:
    """Measure the database clock against this process's (Docker runs a VM).

    Returns:
        The database's ``clock_timestamp()`` minus the local time at the midpoint.

    """
    with engine.connect() as conn:
        before = datetime.now(UTC)
        db_now = conn.scalar(text("SELECT clock_timestamp()"))
        after = datetime.now(UTC)
    assert isinstance(db_now, datetime)
    return db_now - (before + (after - before) / 2)


@dataclass(frozen=True, slots=True)
class AlertOutcome:
    """What the batch left behind once drained."""

    statuses: list[TransactionStatus]
    # processed_at - received_at of every row, in seconds
    processing_s: list[float]
    received: datetime
    last_processed: datetime
    # (rule_hit.created_at, the trigger's received_at) per CASH_STRUCTURING hit
    hits: list[tuple[datetime, datetime]]
    all_hits: int


def _alert_outcome(tenant_id: UUID) -> AlertOutcome:
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.execute(
            select(
                Transaction.status, Transaction.received_at, Transaction.processed_at
            ).where(Transaction.external_id.like("a-%"))
        ).all()
        last_processed = s.scalar(select(func.max(Transaction.processed_at)))
        hits = s.execute(
            select(RuleHit.created_at, Transaction.received_at)
            .join(Transaction, Transaction.id == RuleHit.first_transaction_id)
            .where(RuleHit.rule_code == CASH)
        ).all()
        all_hits = s.scalar(select(func.count()).select_from(RuleHit)) or 0
    assert last_processed is not None
    return AlertOutcome(
        statuses=[status for status, _, _ in rows],
        processing_s=[
            (processed - received).total_seconds()
            for _, received, processed in rows
            if processed is not None
        ],
        received=min(received for _, received, _ in rows),
        last_processed=last_processed,
        hits=[(created, received) for created, received in hits],
        all_hits=all_hits,
    )


def test_alert_latency_p95_over_1000_transactions(
    client: TestClient,
    tenant: CreatedTenant,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
):
    stage = build_stage(tenant, admin_engine, monkeypatch)
    refs = [f"alert-{index:03d}" for index in range(ALERT_RELATIONSHIPS)]
    for ref in refs:
        _ = add_relationship(tenant, ref, "N3")
    writer = machine_headers(tenant, "transactions:write")
    skew = _db_skew(admin_engine)
    # The clock runs at real speed from 14 December, so the processing times the
    # rows record are real and their unit values are B7's fixture's. The queue and
    # the scheduler keep the real clock they were built with.
    offset = ALERT_AT - datetime.now(UTC)
    monkeypatch.setattr(clock, "now", lambda: datetime.now(UTC) + offset)

    live = LiveWorker.start(stage)
    try:
        began = time.perf_counter()
        response = client.post(
            "/v1/transactions",
            json={"transactions": _alert_items(refs)},
            headers={**writer, "Idempotency-Key": "perf-alert-batch"},
        )
        posted = time.perf_counter() - began
        assert response.status_code == 202, response.text
        assert response.json()["accepted"] == ALERT_TRANSACTIONS
        _wait_processed(stage.tenant_id, began=began)
        drained = time.perf_counter() - began
    finally:
        ticks = live.stop()

    outcome = _alert_outcome(stage.tenant_id)
    elapsed = (outcome.last_processed - outcome.received).total_seconds()
    # created_at is the database's now(); received_at is the shifted clock.
    hit_ms = [
        ((created - skew) - (received - offset)).total_seconds() * 1000
        for created, received in outcome.hits
    ]
    record(
        "alert_latency_p95_over_1000_transactions",
        SP04_FILE,
        transactions=ALERT_TRANSACTIONS,
        relationships=ALERT_RELATIONSHIPS,
        post_s=round(posted, 2),
        drain_s=round(drained, 2),
        worker_ticks=ticks,
        tick_seconds=get_settings().worker_tick_seconds,
        received_to_last_processed_s=round(elapsed, 2),
        transactions_per_s=round(ALERT_TRANSACTIONS / elapsed, 1),
        processed_latency_p50_s=round(statistics.median(outcome.processing_s), 2),
        processed_latency_p95_s=round(_quantile(outcome.processing_s, 20, 19), 2),
        nfr_p95_s=5,
        nfr_met=_quantile(outcome.processing_s, 20, 19) <= NFR_ALERT_P95_S,
        structuring_hits=len(outcome.hits),
        all_hits=outcome.all_hits,
        hit_latency_p50_ms=round(statistics.median(hit_ms), 1),
        hit_latency_p95_ms=round(_quantile(hit_ms, 20, 19), 1),
        hit_latency_max_ms=round(max(hit_ms), 1),
        db_clock_skew_ms=round(skew.total_seconds() * 1000, 1),
    )
    assert len(outcome.statuses) == ALERT_TRANSACTIONS
    assert set(outcome.statuses) == {TransactionStatus.PROCESSED}
    assert len(outcome.hits) == STRUCTURING_SCENARIOS
    assert drained < DRAIN_FLOOR_S, f"{drained:.1f} s"


# ---- counterparty first-sight throughput (SP-04 spec §11.1) --------------------------

OFAC = "OFAC_SDN"
COUNTERPARTIES = 1_000
NEAR_SHARE = 20  # one in twenty, 5 %
LIST_ENTRIES = 2_000
RESOLVE_CHUNK = 100
FIRST_SIGHT_FLOOR_PER_S = 20.0
# Every resolution falls in this one minute, so one first-sight job is enqueued.
FIRST_SIGHT_MINUTE = datetime(2026, 10, 20, 15, 4, 12, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Screening:
    tenant: CreatedTenant
    queue: PostgresJobQueue
    store: LocalFsObjectStore


@pytest.fixture
def screening(
    tenant: CreatedTenant, admin_engine: Engine, tmp_path: Path
) -> Iterator[Screening]:
    seed_sources(admin_engine)
    assign_itf(tenant, admin_engine)
    factory = get_sessionmaker()
    queue = PostgresJobQueue(factory)
    reset_subscribers()
    _ = register_screening(
        session_factory=factory, settings=get_settings(), queue=queue
    )
    yield Screening(tenant, queue, LocalFsObjectStore(tmp_path / "objs"))
    reset_subscribers()
    set_index_holder(None)


def _unique(rng: Draw, pool: tuple[tuple[str, ...], tuple[str, ...]], count: int):
    seen: set[Name] = set()
    names: list[str] = []
    while len(names) < count:
        name = draw_name(rng, *pool)
        if name not in seen:
            seen.add(name)
            names.append(" ".join(name))
    return names


def _first_sight_inputs() -> tuple[list[ListEntry], list[CounterpartyInput], set[str]]:
    """Draw OFAC's entries from the lists' pool and the counterparties.

    One counterparty in twenty carries a listed name; the rest are drawn from the
    party pool, which shares no token with the lists.

    Returns:
        The entries, the counterparties in a seeded order, and the near names.

    """
    rng = Draw(SEED + 4)
    party_pool, list_pool = split_pools()
    listed = _unique(rng, list_pool, LIST_ENTRIES)
    entries = [
        ListEntry(
            list_code=OFAC,
            entry_key=f"P{i:04d}",
            entity_type=EntityType.PERSON,
            primary_name=EntryName(name.upper(), NameQuality.PRIMARY),
            source_ref=f"perf P{i:04d}",
        )
        for i, name in enumerate(listed)
    ]
    near = set(rng.sample(listed, COUNTERPARTIES // NEAR_SHARE))
    background = _unique(rng, party_pool, COUNTERPARTIES - len(near))
    names = rng.sample([*near, *background], COUNTERPARTIES)
    inputs = [
        CounterpartyInput(kind=CounterpartyKind.PERSON, name=name, country="MX")
        for name in names
    ]
    return entries, inputs, near


def _resolve_chunk(tenant_id: UUID, inputs: list[CounterpartyInput]) -> None:
    # One transaction per chunk, as an ingest batch resolves its counterparties.
    with tenant_session(system_ctx(tenant_id)) as s:
        for counterparty in inputs:
            _ = resolve(s, counterparty)


def test_counterparty_first_sight_throughput(
    screening: Screening, monkeypatch: pytest.MonkeyPatch
):
    entries, inputs, near = _first_sight_inputs()
    _ = load_shared(OFAC, entries, store=screening.store)
    started = time.perf_counter()
    get_index_holder().warm()
    warmed = time.perf_counter() - started
    tenant_id = screening.tenant.tenant_id

    with monkeypatch.context() as patched:
        patched.setattr(clock, "now", lambda: FIRST_SIGHT_MINUTE)
        started = time.perf_counter()
        for at in range(0, COUNTERPARTIES, RESOLVE_CHUNK):
            _resolve_chunk(tenant_id, inputs[at : at + RESOLVE_CHUNK])
        resolved = time.perf_counter() - started

    runner = JobRunner(
        screening.queue,
        get_sessionmaker(),
        worker_id="w-perf",
        kinds=[FIRST_SIGHT_KIND],
    )
    started = time.perf_counter()
    jobs = _drain(runner)
    elapsed = time.perf_counter() - started

    with tenant_session(system_ctx(tenant_id)) as s:
        rows = list(s.scalars(select(Counterparty)))
        flagged = {
            row.id
            for row in rows
            if row.screening_status is CounterpartyScreeningStatus.PENDING_REVIEW
        }
        near_ids = {
            row.id
            for row in rows
            if row.name is not None and row.name.reveal(session=s) in near
        }
    rate = COUNTERPARTIES / elapsed
    record(
        "counterparty_first_sight_throughput",
        SP04_FILE,
        counterparties=COUNTERPARTIES,
        near=len(near),
        list_entries=LIST_ENTRIES,
        index_load_s=round(warmed, 2),
        resolve_s=round(resolved, 2),
        resolve_per_s=round(COUNTERPARTIES / resolved, 1),
        screen_s=round(elapsed, 2),
        jobs=jobs,
        counterparties_per_s=round(rate, 1),
        pending_review=len(flagged),
        background_pending_review=len(flagged - near_ids),
    )
    assert len(rows) == COUNTERPARTIES
    assert all(row.screened_at is not None for row in rows)
    assert len(near_ids) == len(near)
    assert near_ids <= flagged
    assert rate >= FIRST_SIGHT_FLOOR_PER_S, f"{rate:.1f} counterparties/s"
