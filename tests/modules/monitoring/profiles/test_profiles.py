"""Monthly behaviour profiles (SP-04 spec §5.2, §13.3; plan 4c C8).

``compute_profiles`` sums a relationship's ``PROCESSED`` rows of a Mexico City
month into ``behaviour_profile.metrics`` (MXN minor units as strings, amount shares
per Deviation 13), copies the declared profile, derives the actual-to-declared
ratios and flags a deviation at ``XOLOTL_MONITORING_PROFILE_DEVIATION_RATIO``. Every
row written or changed publishes the staff-only
``monitoring.behaviour_profile_updated.v1``.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select, text, update
from sqlalchemy.orm import Session

from tests.e2e.conftest import drain
from tests.helpers import staff_session, system_ctx
from tests.modules.monitoring.rules.conftest import (
    OCCURRED,
    context,
    enriched,
    rule,
    value,
)
from tests.modules.monitoring.support import add_customer
from tests.modules.party.route_support import machine_headers
from tests.platform.jobs_support import FakeClock
from tests.platform.webhooks_support import Stack, deliveries, make_endpoint
from xolotl.app import wire_platform
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.composition import register_monitoring
from xolotl.modules.monitoring.enums import BatchSource, TransactionStatus
from xolotl.modules.monitoring.events import (
    BEHAVIOUR_PROFILE_UPDATED,
    STAFF_ONLY_EVENT_TYPES,
)
from xolotl.modules.monitoring.profiles import job
from xolotl.modules.monitoring.profiles.job import (
    PROFILES_KIND,
    compute_profiles,
    profile_month,
)
from xolotl.modules.monitoring.profiles.models import BehaviourProfile
from xolotl.modules.monitoring.rules.definitions import RuleKind
from xolotl.modules.monitoring.rules.evaluate import profile_for
from xolotl.modules.monitoring.rules.kinds import DeclaredProfile, evaluate
from xolotl.modules.monitoring.txn.models import (
    Account,
    Transaction,
    TransactionBatch,
)
from xolotl.modules.monitoring.txn.schema import Channel, Direction, Instrument, Rail
from xolotl.modules.party import customer
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.party.schemas import DeclaredProfile as DeclaredProfileInput
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.schema import CounterpartyKind
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.crypto.encrypted import encrypt_json
from xolotl.platform.db import get_engine, get_sessionmaker, tenant_session
from xolotl.platform.events import is_staff_only
from xolotl.platform.events.event import Event, event_from_row
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.events.notifier import InAppNotifier
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.models import JobSchedule
from xolotl.platform.policy.models import PolicySnapshot
from xolotl.platform.storage.local import LocalFsObjectStore

MONTH = "2027-01"
IN = Direction.IN
OUT = Direction.OUT
PROFILES = "/v1/monitoring/profiles"


def _local(month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2027, month, day, hour, minute, tzinfo=MEXICO_CITY_ZONE).astimezone(
        UTC
    )


@dataclass(frozen=True, slots=True)
class Book:
    """A relationship with an account, a batch and a snapshot to hang rows on."""

    tenant_id: UUID
    relationship: CustomerRelationship
    account_id: UUID
    batch_id: UUID
    snapshot_id: UUID

    def add(
        self,
        at: datetime,
        direction: Direction,
        pesos: str,
        *,
        instrument: Instrument = Instrument.TRANSFER,
        rail: Rail = Rail.SPEI,
        country: str | None = None,
        counterparty_id: UUID | None = None,
        status: TransactionStatus = TransactionStatus.PROCESSED,
        original: tuple[int, str] | None = None,
    ) -> UUID:
        """Insert one enriched row of the relationship as processing leaves it.

        ``pesos`` is the row's ``MXN/SAME_DAY`` amount; ``original`` the amount in
        minor units and currency it arrived in, when not pesos.

        Returns:
            The row's id.

        """
        local = at.astimezone(MEXICO_CITY_ZONE).date()
        quarter = (local.month - 1) // 3 + 1
        amount_minor, currency = original or (int(Decimal(pesos) * 100), "MXN")
        with tenant_session(system_ctx(self.tenant_id)) as s:
            row = Transaction(
                tenant_id=self.tenant_id,
                occurred_at=at,
                id=uuid7(),
                external_id=f"ext-{uuid7().hex[-12:]}",
                batch_id=self.batch_id,
                party_id=self.relationship.party_id,
                relationship_id=self.relationship.id,
                account_id=self.account_id,
                product="EMONEY",
                direction=direction,
                rail=rail,
                instrument=instrument,
                channel=Channel.APP,
                amount_minor=amount_minor,
                currency=currency,
                unit_amounts={
                    "MXN/SAME_DAY": {
                        "amount": pesos,
                        "unit_value_id": None,
                        "resolved_date": local.isoformat(),
                    }
                },
                counterparty_id=counterparty_id,
                counterparty_country=country,
                period_keys={
                    "DAY": local.isoformat(),
                    "MONTH": f"{local:%Y-%m}",
                    "QUARTER": f"{local.year}-Q{quarter}",
                    "BUSINESS_DAY": local.weekday() < 5,
                },
                policy_snapshot_id=self.snapshot_id,
                status=status,
                content_hash=b"\x00" * 32,
                raw=encrypt_json({"k": "v"}, tenant_id=self.tenant_id, session=s),
            )
            s.add(row)
            s.flush()
            return row.id

    def counterparty(self) -> UUID:
        # Only the key matters here: a hash stands in for a resolved wallet.
        with tenant_session(system_ctx(self.tenant_id)) as s:
            row = Counterparty(
                tenant_id=self.tenant_id,
                kind=CounterpartyKind.UNKNOWN,
                wallet_hash=uuid7().bytes * 2,
                dedup_hash=uuid7().bytes * 2,
            )
            s.add(row)
            s.flush()
            return row.id


def book(tenant: CreatedTenant, external_ref: str) -> Book:
    relationship = add_customer(tenant, external_ref)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        account = Account(
            tenant_id=tenant.tenant_id,
            relationship_id=relationship.id,
            party_id=relationship.party_id,
            external_ref=f"default:{relationship.id}",
        )
        batch = TransactionBatch(
            tenant_id=tenant.tenant_id,
            source=BatchSource.API,
            submitted_by="test",
            item_count=1,
            accepted=1,
            duplicates=0,
        )
        s.add_all([account, batch])
        snapshot = s.scalar(select(PolicySnapshot))
        if snapshot is None:
            snapshot = PolicySnapshot(
                tenant_id=tenant.tenant_id,
                content_hash=b"\x02" * 32,
                content={"country": "MX"},
            )
            s.add(snapshot)
        s.flush()
        return Book(
            tenant.tenant_id,
            relationship,
            account.id,
            batch.id,
            snapshot.id,
        )


def golden_month(ledger: Book) -> None:
    """Seven rows of January 2027 and three that must not count.

    In: 10,000.00 by SPEI from a Mexican counterparty, USD 500.00 from the same
    one, worth 9,250.00 at its FIX (only its MXN/SAME_DAY amount counts), 5,000.00
    in cash,
    20,000.00 by SWIFT from a US counterparty, 1,000.00 from a Guatemalan one at
    23:30 on 31 January in Mexico City (already February in UTC). Out: 15,000.00
    in crypto and 4,000.50 by SPEI to the Mexican counterparty again.
    """
    a, b, c, d = (ledger.counterparty() for _ in range(4))
    _ = ledger.add(_local(1, 5), IN, "10000.00", country="MX", counterparty_id=a)
    _ = ledger.add(
        _local(1, 5, 11),
        IN,
        "9250.00",
        country="MX",
        counterparty_id=a,
        original=(50_000, "USD"),
    )
    _ = ledger.add(
        _local(1, 5, 15), IN, "5000.00", instrument=Instrument.CASH, rail=Rail.CASH
    )
    _ = ledger.add(
        _local(1, 12), IN, "20000.00", rail=Rail.SWIFT, country="US", counterparty_id=b
    )
    _ = ledger.add(
        _local(1, 31, 23, 30), IN, "1000.00", country="GT", counterparty_id=d
    )
    _ = ledger.add(
        _local(1, 20),
        OUT,
        "15000.00",
        instrument=Instrument.CRYPTO,
        rail=Rail.CRYPTO_ONCHAIN,
        counterparty_id=c,
    )
    _ = ledger.add(_local(1, 20, 16), OUT, "4000.50", country="MX", counterparty_id=a)
    # Not counted: February, not yet processed, an internal movement.
    _ = ledger.add(_local(2, 1, 9), IN, "999.00")
    _ = ledger.add(_local(1, 7), IN, "888.00", status=TransactionStatus.RECEIVED)
    _ = ledger.add(_local(1, 8), Direction.INTERNAL, "777.00")


GOLDEN = {
    "inflow_minor": "4525000",
    "outflow_minor": "1900050",
    "inflow_count": 5,
    "outflow_count": 2,
    "cash_share": "0.0778",
    "international_share": "0.3268",
    "crypto_share": "0.2335",
    "distinct_counterparties": 4,
    "active_days": 4,
    "currency": "MXN",
}


def declare(ledger: Book, inflow: int, outflow: int) -> None:
    profile = DeclaredProfileInput(
        monthly_inflow_minor=inflow, monthly_outflow_minor=outflow, currency="MXN"
    )
    with tenant_session(system_ctx(ledger.tenant_id)) as s:
        _ = customer.set_declared_profile(s, ledger.relationship.id, profile)


def compute(tenant_id: UUID, month: str = MONTH) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return compute_profiles(s, month=month)


def profiles(tenant_id: UUID) -> list[BehaviourProfile]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(
            s.scalars(
                select(BehaviourProfile).order_by(
                    BehaviourProfile.relationship_id, BehaviourProfile.month
                )
            )
        )


def updates(tenant_id: UUID) -> list[Event]:
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(
            select(OutboxRow)
            .where(OutboxRow.type == BEHAVIOUR_PROFILE_UPDATED)
            .order_by(OutboxRow.seq)
        )
        return [event_from_row(row) for row in rows]


@pytest.fixture
def ledger(tenant: CreatedTenant) -> Book:
    return book(tenant, "cust-1")


def test_a_month_of_processed_rows_yields_the_metrics(
    tenant: CreatedTenant, ledger: Book
):
    golden_month(ledger)
    other = book(tenant, "cust-2")
    _ = other.add(_local(1, 9), Direction.OUT, "250.00", instrument=Instrument.CASH)

    assert compute(tenant.tenant_id) == 2

    by_relationship = {row.relationship_id: row for row in profiles(tenant.tenant_id)}
    row = by_relationship[ledger.relationship.id]
    assert (row.month, row.party_id) == (MONTH, ledger.relationship.party_id)
    assert row.metrics == GOLDEN
    assert by_relationship[other.relationship.id].metrics == {
        "inflow_minor": "0",
        "outflow_minor": "25000",
        "inflow_count": 0,
        "outflow_count": 1,
        "cash_share": "1.0000",
        "international_share": "0.0000",
        "crypto_share": "0.0000",
        "distinct_counterparties": 0,
        "active_days": 1,
        "currency": "MXN",
    }
    # A month without activity writes nothing.
    assert compute(tenant.tenant_id, "2026-11") == 0


def test_without_a_declaration_deviation_is_null_and_not_deviated(
    tenant: CreatedTenant, ledger: Book
):
    golden_month(ledger)
    assert compute(tenant.tenant_id) == 1
    (row,) = profiles(tenant.tenant_id)
    assert (row.declared_profile, row.deviation, row.deviated) == (None, None, False)
    (event,) = updates(tenant.tenant_id)
    assert event.data["deviated"] is False


def test_a_ratio_at_or_above_the_threshold_marks_deviated_and_publishes(
    tenant: CreatedTenant, ledger: Book, webhook_stack: Stack
):
    webhook_stack.settle(tenant.tenant_id)
    _ = make_endpoint(tenant.tenant_id, event_types=["*"])
    golden_month(ledger)
    # 4,525,000 over 2,262,500 is exactly 2.0; 1,900,050 over 2,000,000 is 0.95.
    declare(ledger, 2_262_500, 2_000_000)
    assert compute(tenant.tenant_id) == 1

    (row,) = profiles(tenant.tenant_id)
    assert row.declared_profile == {
        "monthly_inflow_minor": 2_262_500,
        "monthly_outflow_minor": 2_000_000,
        "currency": "MXN",
        "expected_countries": [],
        "expected_instruments": [],
    }
    assert row.deviation == {"monthly_inflow": "2.0", "monthly_outflow": "0.9"}
    assert row.deviated is True
    (event,) = updates(tenant.tenant_id)
    assert event.data == {
        "relationship_id": str(ledger.relationship.id),
        "party_id": str(ledger.relationship.party_id),
        "month": MONTH,
        "deviated": True,
    }
    assert event.key == str(ledger.relationship.id)
    assert event.subject == f"behaviour_profile/{row.id}"
    assert BEHAVIOUR_PROFILE_UPDATED in STAFF_ONLY_EVENT_TYPES
    assert is_staff_only(BEHAVIOUR_PROFILE_UPDATED)
    _dispatch(tenant.tenant_id, webhook_stack)
    # The wildcard endpoint gets nothing: the event is staff-only.
    assert deliveries(tenant.tenant_id) == []


def test_a_ratio_below_the_threshold_does_not_deviate(
    tenant: CreatedTenant, ledger: Book
):
    golden_month(ledger)
    # 4,525,000 over 2,262,501 is 1.99999: shown as 1.9, not deviated.
    declare(ledger, 2_262_501, 2_000_000)
    assert compute(tenant.tenant_id) == 1
    (row,) = profiles(tenant.tenant_id)
    assert row.deviation == {"monthly_inflow": "1.9", "monthly_outflow": "0.9"}
    assert row.deviated is False


def test_a_declared_zero_with_activity_deviates_without_a_ratio(
    tenant: CreatedTenant, ledger: Book
):
    golden_month(ledger)
    # Nothing out declared, 19,000.50 out moved: no ratio, but deviated.
    declare(ledger, 9_000_000, 0)
    other = book(tenant, "cust-2")
    _ = other.add(_local(1, 9), Direction.OUT, "250.00")
    # A declared zero in a direction without activity is no deviation.
    declare(other, 0, 100_000)
    assert compute(tenant.tenant_id) == 2

    by_relationship = {row.relationship_id: row for row in profiles(tenant.tenant_id)}
    row = by_relationship[ledger.relationship.id]
    assert row.deviation == {
        "monthly_inflow": "0.5",
        "monthly_outflow": None,
        "reasons": {"monthly_outflow": "DECLARED_ZERO"},
    }
    assert row.deviated is True
    quiet = by_relationship[other.relationship.id]
    assert quiet.deviation == {"monthly_outflow": "0.2"}
    assert quiet.deviated is False


def test_the_threshold_setting_is_honoured(
    tenant: CreatedTenant, ledger: Book, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        get_settings(), "monitoring_profile_deviation_ratio", Decimal("3.0")
    )
    golden_month(ledger)
    declare(ledger, 2_262_500, 2_000_000)
    assert compute(tenant.tenant_id) == 1
    assert profiles(tenant.tenant_id)[0].deviated is False
    # 4,525,000 over 1,508,333 is 3.0000007.
    declare(ledger, 1_508_333, 2_000_000)
    assert compute(tenant.tenant_id) == 1
    (row,) = profiles(tenant.tenant_id)
    assert row.deviation == {"monthly_inflow": "3.0", "monthly_outflow": "0.9"}
    assert row.deviated is True


def test_recomputing_a_month_updates_the_row_and_publishes_only_on_change(
    tenant: CreatedTenant, ledger: Book
):
    golden_month(ledger)
    assert compute(tenant.tenant_id) == 1
    (first,) = profiles(tenant.tenant_id)
    assert compute(tenant.tenant_id) == 1
    assert len(updates(tenant.tenant_id)) == 1
    (same,) = profiles(tenant.tenant_id)
    assert (same.id, same.computed_at) == (first.id, first.computed_at)

    declare(ledger, 1_000_000, 1_000_000)
    assert compute(tenant.tenant_id) == 1
    (changed,) = profiles(tenant.tenant_id)
    assert changed.id == first.id
    assert changed.deviation == {"monthly_inflow": "4.5", "monthly_outflow": "1.9"}
    assert changed.deviated is True
    assert [e.data["deviated"] for e in updates(tenant.tenant_id)] == [False, True]

    # A late row of the month changes the metrics: one more event.
    _ = ledger.add(_local(1, 25), Direction.IN, "100.00")
    assert compute(tenant.tenant_id) == 1
    assert profiles(tenant.tenant_id)[0].metrics["inflow_minor"] == "4535000"
    assert len(updates(tenant.tenant_id)) == 3


def test_a_row_another_computation_inserted_first_is_compared_not_duplicated(
    tenant: CreatedTenant, ledger: Book, monkeypatch: pytest.MonkeyPatch
):
    golden_month(ledger)
    assert compute(tenant.tenant_id) == 1

    # Another run inserted the month's row after this one looked: the insert
    # does nothing and the row is compared as if it had been read.
    def nothing_yet(_s: Session, _month: str) -> dict[UUID, BehaviourProfile]:
        return {}

    monkeypatch.setattr(job, "_existing", nothing_yet)
    assert compute(tenant.tenant_id) == 1
    assert len(profiles(tenant.tenant_id)) == 1
    assert len(updates(tenant.tenant_id)) == 1
    declare(ledger, 2_262_500, 2_000_000)
    assert compute(tenant.tenant_id) == 1
    (row,) = profiles(tenant.tenant_id)
    assert row.deviated is True
    assert [e.data["deviated"] for e in updates(tenant.tenant_id)] == [False, True]


def test_the_month_is_read_through_the_period_index_expression(
    tenant: CreatedTenant, ledger: Book
):
    golden_month(ledger)
    seen: list[str] = []

    def record(*args: object) -> None:
        seen.append(str(args[2]))

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert compute(tenant.tenant_id) == 1
    finally:
        event.remove(engine, "before_cursor_execute", record)
    # ix_transaction_period is built on (period_keys ->> 'MONTH').
    assert any("period_keys ->> 'MONTH'" in statement for statement in seen)


def test_the_previous_mexico_city_month():
    assert profile_month(datetime(2027, 2, 2, tzinfo=UTC).date()) == "2027-01"
    assert profile_month(datetime(2027, 1, 2, tzinfo=UTC).date()) == "2026-12"


def test_the_job_computes_the_previous_mexico_city_month(
    tenant: CreatedTenant, ledger: Book, monkeypatch: pytest.MonkeyPatch
):
    golden_month(ledger)
    _ = ledger.add(_local(2, 1, 9), Direction.OUT, "10.00")
    # 03:00 on 2 February in Mexico City.
    monkeypatch.setattr(clock, "now", lambda: datetime(2027, 2, 2, 9, tzinfo=UTC))
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        schedule = s.scalars(
            select(JobSchedule).where(JobSchedule.name == PROFILES_KIND)
        ).one()
        assert (schedule.kind, schedule.cron, schedule.tz, schedule.payload) == (
            PROFILES_KIND,
            "0 3 2 * *",
            "America/Mexico_City",
            {},
        )
        # Only this schedule is due.
        _ = s.execute(update(JobSchedule).values(enabled=False))
        schedule.enabled = True
        # The scheduler reads the wall clock it was built with, not the frozen one.
        schedule.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    services = wire_platform(get_sessionmaker(), get_settings())
    assert services.scheduler.tick() == 1
    _ = drain(services)
    assert [row.month for row in profiles(tenant.tenant_id)] == [MONTH]

    # The CLI's payload names the month.
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = services.queue.enqueue(s, kind=PROFILES_KIND, payload={"month": "2027-02"})
    _ = drain(services)
    assert [row.month for row in profiles(tenant.tenant_id)] == [MONTH, "2027-02"]


def test_the_profile_endpoint_returns_the_last_twelve_months_newest_first(
    client: TestClient,
    tenant: CreatedTenant,
    ledger: Book,
    admin_headers: dict[str, str],
):
    months = [f"2026-{m:02d}" for m in range(1, 13)] + ["2027-01", "2027-02"]
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        for month in months:
            s.add(
                BehaviourProfile(
                    tenant_id=tenant.tenant_id,
                    relationship_id=ledger.relationship.id,
                    party_id=ledger.relationship.party_id,
                    month=month,
                    metrics={**GOLDEN},
                    declared_profile=None,
                    deviation=None,
                    deviated=month == "2027-02",
                )
            )
    path = f"{PROFILES}/{ledger.relationship.id}"
    resp = client.get(path, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [item["month"] for item in body] == months[::-1][:12]
    assert body[0]["deviated"] is True
    assert body[0]["metrics"] == GOLDEN
    assert set(body[0]) == {
        "month",
        "metrics",
        "declared_profile",
        "deviation",
        "deviated",
        "computed_at",
    }

    unknown = client.get(f"{PROFILES}/{uuid7()}", headers=admin_headers)
    assert (unknown.status_code, unknown.json()["code"]) == (404, "NOT_FOUND")
    # Staff only: no API key reads a profile.
    key = machine_headers(tenant, "parties:read", "transactions:read")
    assert client.get(path, headers=key).status_code == 403


def test_a_relationship_without_profiles_answers_an_empty_list(
    client: TestClient, ledger: Book, admin_headers: dict[str, str]
):
    resp = client.get(f"{PROFILES}/{ledger.relationship.id}", headers=admin_headers)
    assert (resp.status_code, resp.json()) == (200, [])


def test_the_evaluator_profile_carries_the_declaration(ledger: Book):
    definition = rule(RuleKind.PROFILE_DEVIATION)
    mtd = {"mtd_in": value("250000", count=5), "trailing_in": value("250000")}
    txn = enriched(
        party_id=ledger.relationship.party_id,
        relationship_id=ledger.relationship.id,
        occurred_at=OCCURRED,
    )
    with tenant_session(system_ctx(ledger.tenant_id)) as s:
        before = profile_for(s, txn)
    assert before.declared_profile is None
    assert evaluate(definition, context(mtd, txn=txn, who=before)) is None

    # 100,000.00 pesos a month declared; 250,000 pesos month to date is 2.5 times.
    declare(ledger, 10_000_000, 8_000_000)
    with tenant_session(system_ctx(ledger.tenant_id)) as s:
        after = profile_for(s, txn)
    assert after.declared_profile == DeclaredProfile(
        monthly_inflow_minor=10_000_000, monthly_outflow_minor=8_000_000, currency="MXN"
    )
    firing = evaluate(definition, context(mtd, txn=txn, who=after))
    assert firing is not None
    assert firing.rule_code == "DECLARED_VS_ACTUAL_VOLUME"


# ---- the webhook stack ------------------------------------------------------------


@pytest.fixture
def webhook_stack(engine: Engine, tmp_path: Path) -> Iterator[Stack]:
    """Build the webhook stack and compose the monitoring module on its queue.

    Yields:
        The stack; the subscriptions are dropped afterwards.

    """
    reset_subscribers()
    stack = Stack(FakeClock())
    register_monitoring(
        session_factory=get_sessionmaker(),
        settings=get_settings(),
        queue=stack.queue,
        store=LocalFsObjectStore(tmp_path),
        notifier=InAppNotifier(),
    )
    yield stack
    reset_subscribers()


def _dispatch(tenant_id: UUID, stack: Stack) -> None:
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )
    while stack.bus.dispatch_pending():
        pass
