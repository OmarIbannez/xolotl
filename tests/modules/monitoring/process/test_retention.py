"""The monthly retention job (plan 4c C7, SP-04 spec §8.6, Decision 22).

Months are seeded in partitions made for the test and dropped at teardown when the
job left them. The clock is frozen on 2026-12-15, when both packs are in force:
ITF keeps transactions ten years (2016-11 is due, 2016-12 is not), AVI five
(2019-03 is due for an AVI tenant only).
"""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import product
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import ColumnElement, Connection, Engine, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import InstrumentedAttribute, Session

from tests.e2e.conftest import drain
from tests.helpers import new_tenant, staff_session, system_ctx
from tests.modules.monitoring.support import add_customer, ingest_items, make_item
from tests.platform.policy_support import install_both_packs
from xolotl.app import wire_platform
from xolotl.modules.monitoring.aggregates.declarations import WindowKind
from xolotl.modules.monitoring.aggregates.models import (
    AggBucket,
    AggDeclaration,
    AggDistinct,
)
from xolotl.modules.monitoring.captures.enums import CaptureKind, CapturePeriodStatus
from xolotl.modules.monitoring.captures.models import (
    CaptureGroup,
    CapturePeriod,
    ReportableEvent,
)
from xolotl.modules.monitoring.decide.enums import DecisionMode, DecisionOutcome
from xolotl.modules.monitoring.decide.models import TxnDecision
from xolotl.modules.monitoring.enums import BucketKind, ParkReason
from xolotl.modules.monitoring.process import retention
from xolotl.modules.monitoring.process.retention import (
    RETENTION_KIND,
    RetentionJob,
    months_due,
    prune_buckets,
    retention_period,
)
from xolotl.modules.monitoring.rules.enums import HitTransactionRole
from xolotl.modules.monitoring.rules.models import (
    MonitoringRule,
    MonitoringRuleVersion,
    RuleAssignment,
    RuleHit,
    RuleHitTransaction,
)
from xolotl.modules.monitoring.txn.models import (
    Transaction,
    TransactionParked,
    TransactionRef,
)
from xolotl.modules.monitoring.txn.schema import KeyKind, Rail
from xolotl.modules.party.customer import set_legal_hold
from xolotl.modules.party.models import Party
from xolotl.modules.screening.catalog.models import PrivateList, PrivateListEntryRow
from xolotl.modules.screening.counterparty.models import (
    Counterparty,
    CounterpartyNameIndex,
)
from xolotl.modules.screening.engine.enums import (
    DecidedByType,
    Decision,
    HitBand,
    RunMode,
    SubjectKind,
    SubjectStatus,
    TriggerType,
)
from xolotl.modules.screening.engine.models import (
    ScreeningHit,
    ScreeningListSet,
    ScreeningMatch,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.enums import ListKind
from xolotl.modules.screening.review.models import (
    MatchDisposition,
    ScreeningWhitelist,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.config import get_settings
from xolotl.platform.crypto.encrypted import encrypt, encrypt_json
from xolotl.platform.db import Base, get_sessionmaker, tenant_session
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.models import JobSchedule
from xolotl.platform.jobs.queue import Job, PostgresJobQueue
from xolotl.platform.partitions import ensure_partitions, partition_name
from xolotl.platform.policy import assign_pack
from xolotl.platform.policy.models import PolicySnapshot, TenantPackAssignment

ITF = "MX/ITF-IFPE"
AVI = "MX/LFPIORPI-AVI"
# 12:00 in Mexico City on 2026-12-15.
NOW = datetime(2026, 12, 15, 18, 0, tzinfo=UTC)
OLD = date(2016, 11, 1)  # its end plus ten years is 2026-12-01: due
NEXT = date(2016, 12, 1)  # its end plus ten years is 2027-01-01: kept
MID = date(2019, 3, 1)  # past five years, inside ten
MONTHS = (OLD, NEXT, MID)
TABLES = ("transaction", "txn_decision")
RATIONALE = "same wallet as the list entry"


def _at(month: date, day: int) -> datetime:
    return datetime(month.year, month.month, day, 15, 0, tzinfo=UTC)


def _freeze(monkeypatch: pytest.MonkeyPatch, at: datetime) -> None:
    monkeypatch.setattr(clock, "now", lambda: at)


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> datetime:
    _freeze(monkeypatch, NOW)
    return NOW


@pytest.fixture
def old_partitions(admin_engine: Engine) -> Iterator[None]:
    """Create both tables' partitions of the test months; drop what is left after.

    Yields:
        Nothing.

    """
    with admin_engine.begin() as conn:
        for table in TABLES:
            for month in MONTHS:
                _ = ensure_partitions(conn, table=table, start=month, months_ahead=0)
    try:
        yield
    finally:
        with admin_engine.begin() as conn:
            for table, month in product(TABLES, MONTHS):
                _drop_partition(conn, table, partition_name(table, month))


def _drop_partition(conn: Connection, table: str, name: str) -> None:
    if conn.scalar(text("SELECT to_regclass(:name)"), {"name": name}) is None:
        return
    _ = conn.execute(text(f"TRUNCATE {name} CASCADE"))
    _ = conn.execute(text(f"ALTER TABLE {table} DETACH PARTITION {name}"))
    _ = conn.execute(text(f"DROP TABLE {name}"))


@pytest.fixture
def packs(admin_engine: Engine) -> dict[str, UUID]:
    return install_both_packs(admin_engine)


def _assign(tenant: CreatedTenant, version_id: UUID, product: str | None) -> None:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = assign_pack(
            s,
            pack_version_id=version_id,
            product=product,
            effective_from=date(2026, 1, 1),
            approved_by=tenant.admin_user_id,
        )


@pytest.fixture
def itf(tenant: CreatedTenant, packs: dict[str, UUID]) -> CreatedTenant:
    _assign(tenant, packs[ITF], None)
    return tenant


@pytest.fixture
def avi(engine: Engine, packs: dict[str, UUID]) -> CreatedTenant:
    other = new_tenant("avi", "admin@avi.mx")
    _assign(other, packs[AVI], None)
    return other


@pytest.fixture
def queue(engine: Engine) -> PostgresJobQueue:
    return PostgresJobQueue(get_sessionmaker())


@dataclass(frozen=True, slots=True)
class Txn:
    id: UUID
    occurred_at: datetime
    counterparty_id: UUID | None


@dataclass(frozen=True, slots=True)
class Month:
    """What ``Seeder.month`` wrote for one month of one tenant."""

    txns: tuple[Txn, Txn]
    hit_id: UUID
    group_id: UUID
    decision_id: UUID


class Seeder:
    """Writes a tenant's transactions and what hangs off them."""

    def __init__(self, tenant: CreatedTenant, queue: PostgresJobQueue) -> None:
        self.tenant: CreatedTenant = tenant
        self.queue: PostgresJobQueue = queue
        relationship = add_customer(tenant, "cust-1")
        self.relationship_id: UUID = relationship.id
        self.party_id: UUID = relationship.party_id
        self._seq: int = 0
        with self.session() as s:
            snapshot = PolicySnapshot(
                tenant_id=tenant.tenant_id,
                content_hash=uuid7().bytes * 2,
                content={"on": "2026-01-01"},
            )
            rule = MonitoringRule(
                tenant_id=tenant.tenant_id,
                code="CASH_STRUCTURING",
                family="ALERT",
                kind="THRESHOLD",
                title="Structuring",
                created_by=tenant.admin_user_id,
            )
            s.add_all([snapshot, rule])
            s.flush()
            version = MonitoringRuleVersion(
                tenant_id=tenant.tenant_id,
                rule_id=rule.id,
                version=1,
                definition={"code": rule.code},
                definition_hash=b"\x02" * 32,
                author_id=tenant.admin_user_id,
            )
            s.add(version)
            s.flush()
            assignment = RuleAssignment(
                tenant_id=tenant.tenant_id,
                rule_version_id=version.id,
                effective_from=date(2016, 1, 1),
                approved_by=tenant.admin_user_id,
            )
            s.add(assignment)
            s.flush()
            self.snapshot_id: UUID = snapshot.id
            self.version_id: UUID = version.id
            self.assignment_id: UUID = assignment.id

    def session(self) -> AbstractContextManager[Session]:
        return staff_session(self.tenant.tenant_id, self.tenant.admin_user_id)

    def txn(self, at: datetime, *, counterparty: str | None = None) -> Txn:
        """Ingest one transaction occurred at ``at``, as a client would send it.

        Returns:
            Its id, time and counterparty.

        """
        self._seq += 1
        overrides: dict[str, object] = {
            "external_id": f"tx-{self._seq}",
            "occurred_at": at.isoformat(),
        }
        if counterparty is not None:
            overrides["counterparty"] = {"name": counterparty}
        with self.session() as s:
            report = ingest_items(s, [make_item(**overrides)], queue=self.queue)
            (txn_id,) = report.transaction_ids
            row = s.scalars(select(Transaction).where(Transaction.id == txn_id)).one()
            return Txn(row.id, row.occurred_at, row.counterparty_id)

    def hit(self, trigger: Txn, *links: tuple[Txn, HitTransactionRole]) -> UUID:
        with self.session() as s:
            hit = RuleHit(
                tenant_id=self.tenant.tenant_id,
                rule_version_id=self.version_id,
                rule_code="CASH_STRUCTURING",
                key_kind=KeyKind.PARTY,
                key=str(self.party_id),
                dedup_key=f"PARTY:{self.party_id}:{uuid4().hex}",
                party_id=self.party_id,
                relationship_id=self.relationship_id,
                severity="HIGH",
                window_start=trigger.occurred_at - timedelta(days=30),
                window_end=trigger.occurred_at,
                evidence={"v": 1},
                policy_snapshot_id=self.snapshot_id,
                definition_hash=b"\x02" * 32,
                assignment_id=self.assignment_id,
                first_transaction_id=trigger.id,
                first_occurred_at=trigger.occurred_at,
            )
            s.add(hit)
            s.flush()
            for member, role in ((trigger, HitTransactionRole.TRIGGER), *links):
                s.add(
                    RuleHitTransaction(
                        tenant_id=self.tenant.tenant_id,
                        hit_id=hit.id,
                        transaction_id=member.id,
                        occurred_at=member.occurred_at,
                        role=role,
                    )
                )
            return hit.id

    def group(self, start: datetime) -> UUID:
        with self.session() as s:
            group = CaptureGroup(
                tenant_id=self.tenant.tenant_id,
                capture_code="AVI_XVI_A",
                key_kind=KeyKind.RELATIONSHIP,
                key=str(self.relationship_id),
                period_key=f"{start:%Y-%m}",
                window_start=start,
                window_end=start + timedelta(days=180),
            )
            s.add(group)
            s.flush()
            return group.id

    def event(self, txn: Txn, group_id: UUID | None) -> UUID:
        with self.session() as s:
            event = ReportableEvent(
                tenant_id=self.tenant.tenant_id,
                capture_code="ITF_ROR_CASH",
                report_code="MX_F41_ROR",
                capture_kind=CaptureKind.THRESHOLD,
                transaction_id=txn.id,
                occurred_at=txn.occurred_at,
                party_id=self.party_id,
                relationship_id=self.relationship_id,
                period_key=f"{txn.occurred_at:%Y-%m}",
                capture_group_id=group_id,
                fields={"tipo_operacion": "01"},
                unit_values={},
                policy_snapshot_id=self.snapshot_id,
            )
            s.add(event)
            s.flush()
            return event.id

    def decision(self, decided_at: datetime, txn: Txn | None) -> UUID:
        with self.session() as s:
            decision = TxnDecision(
                tenant_id=self.tenant.tenant_id,
                decided_at=decided_at,
                external_id=f"dec-{uuid4().hex[:12]}",
                rail=Rail.CASH,
                relationship_id=self.relationship_id,
                decision=DecisionOutcome.ALLOW,
                mode=DecisionMode.EVALUATED,
                reasons=[],
                ruleset_hash=b"\x00" * 32,
                latency_ms=5,
                db_ms=1,
                actor="api_client:test",
                transaction_id=None if txn is None else txn.id,
                occurred_at=None if txn is None else txn.occurred_at,
            )
            s.add(decision)
            s.flush()
            if txn is not None:
                _ = s.execute(
                    update(Transaction)
                    .where(Transaction.id == txn.id)
                    .values(decision_id=decision.id, decided_at=decided_at)
                )
            return decision.id

    def park(self, txn: Txn) -> None:
        with self.session() as s:
            s.add(
                TransactionParked(
                    tenant_id=self.tenant.tenant_id,
                    transaction_id=txn.id,
                    occurred_at=txn.occurred_at,
                    reason=ParkReason.UNIT_VALUE_MISSING,
                    unit_code="UDI",
                    needed_on=txn.occurred_at.date(),
                )
            )

    def month(self, month: date) -> Month:
        """Seed a month: two transactions, a hit, captures, a decision, a parked row.

        Returns:
            What was written.

        """
        first = self.txn(_at(month, 3))
        second = self.txn(_at(month, 20))
        hit_id = self.hit(first, (second, HitTransactionRole.WINDOW))
        group_id = self.group(_at(month, 1))
        _ = self.event(first, group_id)
        _ = self.event(second, None)
        decision_id = self.decision(_at(month, 20) - timedelta(minutes=1), second)
        self.park(first)
        return Month((first, second), hit_id, group_id, decision_id)

    def hold(self) -> None:
        with self.session() as s:
            _ = s.execute(
                update(Party).where(Party.id == self.party_id).values(legal_hold=True)
            )


def count(tenant: CreatedTenant, model: type[Base], *where: ColumnElement[bool]) -> int:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return s.scalar(select(func.count()).select_from(model).where(*where)) or 0


def run_retention(tenant: CreatedTenant) -> None:
    """Run one ``monitoring.retention`` job for ``tenant``, as the runner would."""
    job = Job(
        id=uuid7(),
        tenant_id=tenant.tenant_id,
        kind=RETENTION_KIND,
        key=None,
        payload={},
        attempts=1,
        max_attempts=8,
        lock_token=uuid7(),
    )
    with tenant_session(system_ctx(tenant.tenant_id, "job:retention")) as s:
        RetentionJob(session_factory=get_sessionmaker()).run(s, job)


def audits(
    tenant: CreatedTenant, action: str = "monitoring.retention_ran"
) -> list[dict[str, Any]]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        rows = s.scalars(
            select(AuditEventRow)
            .where(AuditEventRow.action == action)
            .order_by(AuditEventRow.seq)
        ).all()
        for row in rows:
            assert (row.entity_type, row.entity_id) == ("tenant", str(tenant.tenant_id))
        return [row.detail or {} for row in rows]


def partitions_left(admin_engine: Engine, month: date) -> list[str]:
    names = [partition_name(table, month) for table in TABLES]
    with admin_engine.connect() as conn:
        return sorted(
            conn.scalars(
                text("SELECT relname FROM pg_class WHERE relname = ANY(:names)"),
                {"names": names},
            ).all()
        )


def in_month(
    column: InstrumentedAttribute[datetime], month: date
) -> ColumnElement[bool]:
    start = datetime(month.year, month.month, 1, tzinfo=UTC)
    end = datetime(
        month.year + (month.month == 12), month.month % 12 + 1, 1, tzinfo=UTC
    )
    return (column >= start) & (column < end)


def test_the_retention_period_is_the_longest_in_force(
    monkeypatch: pytest.MonkeyPatch,
    tenant: CreatedTenant,
    avi: CreatedTenant,
    packs: dict[str, UUID],
):
    _assign(tenant, packs[ITF], "EMONEY")
    _assign(tenant, packs[AVI], "CRYPTO")
    _freeze(monkeypatch, NOW)
    today = NOW.date()
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert retention_period(s, today=today, since=OLD) == "P10Y"
    with tenant_session(system_ctx(avi.tenant_id)) as s:
        assert retention_period(s, today=today, since=OLD) == "P5Y"
        # Before AVI's pack takes effect nothing is in force: nothing is due.
        assert retention_period(s, today=date(2026, 11, 1), since=OLD) is None


def test_a_month_inside_the_retention_period_is_kept(
    monkeypatch: pytest.MonkeyPatch,
    itf: CreatedTenant,
    queue: PostgresJobQueue,
    old_partitions: None,
    admin_engine: Engine,
):
    seeded = Seeder(itf, queue).month(OLD)
    # Ten years after the month's end, minus a day (2026-11-30 in Mexico City).
    _freeze(monkeypatch, datetime(2026, 12, 1, 5, 59, tzinfo=UTC))
    with tenant_session(system_ctx(itf.tenant_id)) as s:
        assert OLD not in months_due(s, today=date(2026, 11, 30), period="P10Y")
    run_retention(itf)
    assert count(itf, Transaction) == 2
    assert count(itf, RuleHit, RuleHit.id == seeded.hit_id) == 1
    assert count(itf, TxnDecision) == 1
    assert partitions_left(admin_engine, OLD) == [
        partition_name("transaction", OLD),
        partition_name("txn_decision", OLD),
    ]
    assert audits(itf)[-1]["retired"] == []
    # On the day the period ends, the month goes.
    _freeze(monkeypatch, datetime(2026, 12, 1, 6, 0, tzinfo=UTC))
    run_retention(itf)
    assert count(itf, Transaction) == 0
    assert partitions_left(admin_engine, OLD) == []


def test_a_past_month_is_retired_in_dependency_order_and_its_partitions_dropped(
    frozen: datetime,
    itf: CreatedTenant,
    queue: PostgresJobQueue,
    old_partitions: None,
    admin_engine: Engine,
):
    seeder = Seeder(itf, queue)
    old = seeder.month(OLD)
    nxt = seeder.month(NEXT)
    # A hit of the old month with a WINDOW link into the next one, and a hit of the
    # next month with a WINDOW link back into the old one.
    spanning = seeder.hit(old.txns[0], (nxt.txns[0], HitTransactionRole.WINDOW))
    later = seeder.hit(nxt.txns[1], (old.txns[1], HitTransactionRole.WINDOW))
    # A decision of the next month linked to an old transaction, and one of the old
    # month linked to a transaction of the next.
    kept_decision = seeder.decision(_at(NEXT, 2), old.txns[0])
    _ = seeder.decision(_at(OLD, 28), nxt.txns[0])

    run_retention(itf)

    assert partitions_left(admin_engine, OLD) == []
    assert partitions_left(admin_engine, NEXT) == [
        partition_name("transaction", NEXT),
        partition_name("txn_decision", NEXT),
    ]
    assert count(itf, Transaction) == 2
    assert count(itf, Transaction, in_month(Transaction.occurred_at, NEXT)) == 2
    assert count(itf, TransactionRef) == 2
    assert count(itf, TransactionParked) == 1
    with tenant_session(system_ctx(itf.tenant_id)) as s:
        hits = set(s.scalars(select(RuleHit.id)))
        links = s.execute(
            select(RuleHitTransaction.hit_id, RuleHitTransaction.role)
        ).all()
        events = s.scalars(select(ReportableEvent.occurred_at)).all()
        groups = set(s.scalars(select(CaptureGroup.id)))
        decisions = {
            row.id: row.transaction_id for row in s.scalars(select(TxnDecision))
        }
        linked = {
            row.id: row.decision_id
            for row in s.scalars(select(Transaction).order_by(Transaction.occurred_at))
        }
    assert hits == {nxt.hit_id, later}
    assert sorted((hit_id, str(role)) for hit_id, role in links) == sorted([
        (nxt.hit_id, "TRIGGER"),
        (nxt.hit_id, "WINDOW"),
        (later, "TRIGGER"),
    ])
    assert spanning not in hits
    assert all(at.month == NEXT.month for at in events)
    assert len(events) == 2
    assert groups == {nxt.group_id}
    # The next month's decisions stay; the one that pointed at an old transaction
    # lost its link, and the next month's transaction lost its old decision.
    assert decisions == {nxt.decision_id: nxt.txns[1].id, kept_decision: None}
    assert linked == {nxt.txns[0].id: None, nxt.txns[1].id: nxt.decision_id}

    (detail,) = audits(itf)
    assert detail == {
        "period": "P10Y",
        "retired": [
            {
                "month": "2016-11",
                "hit_links": 5,
                "events": 2,
                "event_links": 0,
                "groups": 1,
                "hits": 2,
                "decision_links": 5,
                "decisions": 2,
                "parked": 1,
                "refs": 2,
                "transactions": 2,
                "partitions_dropped": [
                    partition_name("txn_decision", OLD),
                    partition_name("transaction", OLD),
                ],
            }
        ],
        "skipped": [],
        "counterparties_purged": 0,
        "buckets_pruned": 0,
    }


def test_a_month_with_a_held_party_is_skipped_and_named(
    frozen: datetime,
    itf: CreatedTenant,
    queue: PostgresJobQueue,
    old_partitions: None,
    admin_engine: Engine,
):
    seeder = Seeder(itf, queue)
    _ = seeder.month(OLD)
    seeder.hold()
    run_retention(itf)
    assert count(itf, Transaction) == 2
    assert count(itf, RuleHit) == 1
    assert count(itf, TxnDecision) == 1
    assert count(itf, ReportableEvent) == 2
    assert len(partitions_left(admin_engine, OLD)) == 2
    (detail,) = audits(itf)
    assert detail["retired"] == []
    assert detail["skipped"] == ["2016-11"]


def test_each_step_commits_on_its_own(
    frozen: datetime,
    itf: CreatedTenant,
    queue: PostgresJobQueue,
    old_partitions: None,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
):
    _ = Seeder(itf, queue).month(OLD)

    def broken(s: Session, month: date) -> dict[str, int]:
        msg = "the database went away"
        raise RuntimeError(msg)

    monkeypatch.setattr(f"{retention.__name__}._delete_refs", broken)
    with pytest.raises(RuntimeError, match="went away"):
        run_retention(itf)
    # The steps before the refs are committed; the refs and the month's rows stay.
    assert count(itf, RuleHitTransaction) == 0
    assert count(itf, RuleHit) == 0
    assert count(itf, ReportableEvent) == 0
    assert count(itf, TxnDecision) == 0
    assert count(itf, TransactionParked) == 0
    assert count(itf, TransactionRef) == 2
    assert count(itf, Transaction) == 2
    assert len(partitions_left(admin_engine, OLD)) == 2
    # Every committed step is audited in its own transaction; the run's summary
    # row went down with the failure.
    assert audits(itf) == []
    steps = audits(itf, "monitoring.retention_step")
    assert [step["step"] for step in steps] == [
        "hit_links",
        "events",
        "hits",
        "decision_links",
        "decisions",
        "parked",
    ]
    assert steps[0] == {"month": "2016-11", "step": "hit_links", "hit_links": 2}
    assert steps[2] == {"month": "2016-11", "step": "hits", "hits": 1, "hit_links": 0}
    assert steps[-1] == {"month": "2016-11", "step": "parked", "parked": 1}

    monkeypatch.undo()
    _freeze(monkeypatch, NOW)
    run_retention(itf)
    assert count(itf, TransactionRef) == 0
    assert count(itf, Transaction) == 0
    assert partitions_left(admin_engine, OLD) == []
    (detail,) = audits(itf)
    (month,) = detail["retired"]
    assert (month["refs"], month["transactions"], month["hits"]) == (2, 2, 0)
    steps = audits(itf, "monitoring.retention_step")
    names = [step["step"] for step in steps]
    assert names[-4:] == ["refs", "transactions", "partitions", "partitions"]
    # Each drop is audited in the transaction that dropped it.
    assert {step.get("partition") for step in steps[-2:]} == {
        partition_name("txn_decision", OLD),
        partition_name("transaction", OLD),
    }


def test_a_partition_is_dropped_only_once_every_tenant_has_retired_its_month(
    frozen: datetime,
    itf: CreatedTenant,
    avi: CreatedTenant,
    queue: PostgresJobQueue,
    old_partitions: None,
    admin_engine: Engine,
):
    # A partition holds every tenant's rows of its month: the AVI tenant (five
    # years) retires its own rows, and the ITF tenant's (ten years) stay.
    ten, five = Seeder(itf, queue), Seeder(avi, queue)
    for seeder in (ten, five):
        _ = seeder.month(OLD)
        _ = seeder.month(MID)

    run_retention(avi)
    assert count(avi, Transaction) == 0
    assert count(avi, TxnDecision) == 0
    assert count(itf, Transaction) == 4
    assert count(itf, TxnDecision) == 2
    assert count(itf, RuleHit) == 2
    assert len(partitions_left(admin_engine, OLD)) == 2
    assert len(partitions_left(admin_engine, MID)) == 2
    # December 2016 holds no row, but ten years have not passed for the ITF tenant.
    assert len(partitions_left(admin_engine, NEXT)) == 2
    (five_ran,) = audits(avi)
    assert [m["month"] for m in five_ran["retired"]] == ["2016-11", "2019-03"]
    assert all(m["partitions_dropped"] == [] for m in five_ran["retired"])

    run_retention(itf)
    # The ITF tenant's old month goes, and with it the now empty partitions; its
    # 2019 month is inside ten years and stays, and so do the partitions.
    assert count(itf, Transaction) == 2
    assert count(itf, Transaction, in_month(Transaction.occurred_at, MID)) == 2
    assert partitions_left(admin_engine, OLD) == []
    assert len(partitions_left(admin_engine, MID)) == 2
    (ten_ran,) = audits(itf)
    assert [m["month"] for m in ten_ran["retired"]] == ["2016-11"]
    assert ten_ran["retired"][0]["partitions_dropped"] == [
        partition_name("txn_decision", OLD),
        partition_name("transaction", OLD),
    ]


def test_a_held_party_of_another_tenant_keeps_the_partition(
    frozen: datetime,
    itf: CreatedTenant,
    packs: dict[str, UUID],
    queue: PostgresJobQueue,
    old_partitions: None,
    admin_engine: Engine,
):
    other = new_tenant("held", "admin@held.mx")
    _assign(other, packs[ITF], None)
    mine, theirs = Seeder(itf, queue), Seeder(other, queue)
    _ = mine.month(OLD)
    _ = theirs.month(OLD)
    theirs.hold()
    run_retention(itf)
    run_retention(other)
    assert count(itf, Transaction) == 0
    assert count(other, Transaction) == 2
    assert len(partitions_left(admin_engine, OLD)) == 2
    assert audits(other)[0]["skipped"] == ["2016-11"]


def _screen(
    seeder: Seeder, counterparty_id: UUID, *, decided_at: datetime
) -> dict[str, UUID]:
    """Give a counterparty a subject, a run, a finding, a hit and a disposition.

    Returns:
        The ids written, by table.

    """
    tenant_id = seeder.tenant.tenant_id
    with seeder.session() as s:
        subject = ScreeningSubject(
            tenant_id=tenant_id,
            kind=SubjectKind.COUNTERPARTY,
            counterparty_id=counterparty_id,
            created_at=decided_at,
        )
        list_set = ScreeningListSet(tenant_id=tenant_id, content_hash=uuid7().bytes * 2)
        private = s.scalar(select(PrivateList)) or PrivateList(
            tenant_id=tenant_id,
            list_code="MX_LPB",
            list_kind=ListKind.BLOCKED,
            title="Lista de personas bloqueadas",
            created_by="staff:test",
        )
        s.add_all([subject, list_set, private])
        s.flush()
        _ = s.execute(
            update(Counterparty)
            .where(Counterparty.id == counterparty_id)
            .values(screening_subject_id=subject.id)
        )
        entry = PrivateListEntryRow(
            tenant_id=tenant_id,
            list_id=private.id,
            source_entry_key=f"clave {uuid4().hex}",
            content_tag=b"\x00" * 32,
            entity_type="PERSON",
            entry=encrypt_json({"k": "v"}, tenant_id=tenant_id, session=s),
            from_seq=1,
        )
        s.add(entry)
        s.flush()
        run = ScreeningRun(
            tenant_id=tenant_id,
            subject_id=subject.id,
            mode=RunMode.COUNTERPARTY,
            trigger_type=TriggerType.EVENT,
            list_set_id=list_set.id,
            policy_snapshot_id=seeder.snapshot_id,
            matcher_version="test/1",
            scoring_config_hash=b"\x00" * 32,
            query_fingerprint=b"\x00" * 32,
            candidate_count=1,
            hit_count=1,
            new_match_count=1,
            outcome=SubjectStatus.PENDING_REVIEW,
            latency_ms=1,
            created_by="job:test",
            created_at=decided_at,
        )
        s.add(run)
        s.flush()
        match = ScreeningMatch(
            tenant_id=tenant_id,
            subject_id=subject.id,
            list_code="MX_LPB",
            list_kind=ListKind.BLOCKED,
            private_list_entry_id=entry.id,
            source_entry_key=entry.source_entry_key,
            entry_content_hash=entry.content_tag,
            band=HitBand.ALERT,
            score=Decimal("0.9500"),
            origin_run_id=run.id,
            created_at=decided_at,
            updated_at=decided_at,
        )
        s.add(match)
        s.flush()
        hit = ScreeningHit(
            tenant_id=tenant_id,
            run_id=run.id,
            list_code="MX_LPB",
            private_list_entry_id=entry.id,
            source_entry_key=entry.source_entry_key,
            entry_content_hash=entry.content_tag,
            score=Decimal("0.9500"),
            band=HitBand.ALERT,
            features={"score": 0.95},
            match_id=match.id,
        )
        disposition = MatchDisposition(
            tenant_id=tenant_id,
            match_id=match.id,
            decision=Decision.FALSE_POSITIVE,
            rationale=encrypt(RATIONALE, tenant_id=tenant_id, session=s),
            decided_by_type=DecidedByType.STAFF,
            decided_by=seeder.tenant.admin_user_id,
            policy_snapshot_id=seeder.snapshot_id,
            decided_at=decided_at,
        )
        s.add_all([hit, disposition])
        s.flush()
        whitelist = ScreeningWhitelist(
            tenant_id=tenant_id,
            subject_id=subject.id,
            list_code=match.list_code,
            source_entry_key=match.source_entry_key,
            entry_content_hash=match.entry_content_hash,
            disposition_id=disposition.id,
            created_at=decided_at,
        )
        s.add(whitelist)
        s.flush()
        return {
            "subject": subject.id,
            "run": run.id,
            "match": match.id,
            "hit": hit.id,
            "disposition": disposition.id,
            "whitelist": whitelist.id,
        }


def _screening_rows(tenant: CreatedTenant, ids: dict[str, UUID]) -> dict[str, int]:
    return {
        "subject": count(
            tenant, ScreeningSubject, ScreeningSubject.id == ids["subject"]
        ),
        "run": count(tenant, ScreeningRun, ScreeningRun.id == ids["run"]),
        "match": count(tenant, ScreeningMatch, ScreeningMatch.id == ids["match"]),
        "hit": count(tenant, ScreeningHit, ScreeningHit.id == ids["hit"]),
        "disposition": count(
            tenant, MatchDisposition, MatchDisposition.id == ids["disposition"]
        ),
        "whitelist": count(
            tenant, ScreeningWhitelist, ScreeningWhitelist.id == ids["whitelist"]
        ),
    }


def test_orphaned_counterparties_go_with_their_subjects_findings_and_dispositions(
    frozen: datetime,
    itf: CreatedTenant,
    queue: PostgresJobQueue,
    old_partitions: None,
):
    seeder = Seeder(itf, queue)
    gone = seeder.txn(_at(OLD, 5), counterparty="Ana Pérez Gómez")
    live = seeder.txn(_at(NEXT, 5), counterparty="Beto Ruiz Soto")
    assert gone.counterparty_id is not None
    assert live.counterparty_id is not None
    # Every screening record of the orphan is itself past ten years.
    ids = _screen(seeder, gone.counterparty_id, decided_at=_at(OLD, 6))
    assert count(itf, CounterpartyNameIndex) == 2

    run_retention(itf)

    assert count(itf, Counterparty, Counterparty.id == gone.counterparty_id) == 0
    assert set(_screening_rows(itf, ids).values()) == {0}
    assert (
        count(
            itf,
            CounterpartyNameIndex,
            CounterpartyNameIndex.counterparty_id == gone.counterparty_id,
        )
        == 0
    )
    with tenant_session(system_ctx(itf.tenant_id)) as s:
        kept = s.get_one(Counterparty, (itf.tenant_id, live.counterparty_id))
        assert kept.txn_count == 1
    assert count(itf, CounterpartyNameIndex) == 1
    assert audits(itf)[0]["counterparties_purged"] == 1
    (purge,) = [
        step
        for step in audits(itf, "monitoring.retention_step")
        if step["step"] == "counterparties"
    ]
    assert purge == {
        "step": "counterparties",
        "counterparties": 1,
        "subjects": 1,
        "runs": 1,
        "findings": 1,
        "screening_hits": 1,
        "dispositions": 1,
        "allow_list": 1,
        "name_index": 1,
    }


def test_the_purge_is_audited_even_when_a_later_step_fails(
    frozen: datetime,
    itf: CreatedTenant,
    queue: PostgresJobQueue,
    old_partitions: None,
    monkeypatch: pytest.MonkeyPatch,
):
    seeder = Seeder(itf, queue)
    gone = seeder.txn(_at(OLD, 5), counterparty="Ana Pérez Gómez")
    assert gone.counterparty_id is not None
    _ = _screen(seeder, gone.counterparty_id, decided_at=_at(OLD, 6))

    def broken(s: Session, *, now: datetime) -> int:
        msg = "the bucket pruning failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(f"{retention.__name__}.prune_buckets", broken)
    with pytest.raises(RuntimeError, match="pruning failed"):
        run_retention(itf)
    assert count(itf, Counterparty, Counterparty.id == gone.counterparty_id) == 0
    assert audits(itf) == []
    steps = audits(itf, "monitoring.retention_step")
    purged = [step for step in steps if step["step"] == "counterparties"]
    assert [step["dispositions"] for step in purged] == [1]


def test_a_counterparty_with_recent_screening_records_is_kept(
    frozen: datetime,
    itf: CreatedTenant,
    queue: PostgresJobQueue,
    old_partitions: None,
):
    # Its transactions are past ten years, but last month's disposition is not.
    seeder = Seeder(itf, queue)
    gone = seeder.txn(_at(OLD, 5), counterparty="Ana Pérez Gómez")
    assert gone.counterparty_id is not None
    ids = _screen(seeder, gone.counterparty_id, decided_at=NOW - timedelta(days=30))

    run_retention(itf)

    assert count(itf, Transaction) == 0
    with tenant_session(system_ctx(itf.tenant_id)) as s:
        kept = s.get_one(Counterparty, (itf.tenant_id, gone.counterparty_id))
        assert kept.txn_count == 0
    assert set(_screening_rows(itf, ids).values()) == {1}
    assert audits(itf)[0]["counterparties_purged"] == 0


def _bucket(
    tenant_id: UUID,
    kind: BucketKind,
    start: datetime,
    end: datetime | None = None,
    agg_id: str = "0123456789abcdef",
) -> AggBucket:
    return AggBucket(
        tenant_id=tenant_id,
        key_kind=KeyKind.PARTY,
        key=str(uuid7()),
        agg_id=agg_id,
        bucket_start=start,
        bucket_kind=kind,
        bucket_end=end,
    )


def test_stale_buckets_are_pruned_except_open_accumulators_and_open_periods(
    frozen: datetime, itf: CreatedTenant
):
    tenant_id = itf.tenant_id
    # The longest window is ACCUM P6M; with the late days, buckets that ended
    # before 2026-06-08 go.
    declarations = [
        ("a" * 16, WindowKind.DURATION, "P30D", BucketKind.DAY),
        ("b" * 16, WindowKind.ACCUM, "P6M", BucketKind.ACCUM),
        ("c" * 16, WindowKind.MONTH, "MONTH", BucketKind.MONTH),
    ]
    mexico = timedelta(hours=6)
    old_day = _bucket(tenant_id, BucketKind.DAY, datetime(2026, 1, 10, tzinfo=UTC))
    recent_day = _bucket(tenant_id, BucketKind.DAY, datetime(2026, 12, 1, tzinfo=UTC))
    open_accum = _bucket(
        tenant_id,
        BucketKind.ACCUM,
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )
    ended_accum = _bucket(
        tenant_id,
        BucketKind.ACCUM,
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 7, 1, tzinfo=UTC),
    )
    open_month = _bucket(
        tenant_id, BucketKind.MONTH, datetime(2026, 1, 1, tzinfo=UTC) + mexico
    )
    closed_month = _bucket(
        tenant_id, BucketKind.MONTH, datetime(2025, 12, 1, tzinfo=UTC) + mexico
    )
    buckets = [old_day, recent_day, open_accum, ended_accum, open_month, closed_month]
    with staff_session(tenant_id, itf.admin_user_id) as s:
        for agg_id, window_kind, window, bucket_kind in declarations:
            s.add(
                AggDeclaration(
                    tenant_id=tenant_id,
                    agg_id=agg_id,
                    declaration={"name": agg_id},
                    bucket_kind=bucket_kind,
                    window_kind=window_kind,
                    window=window,
                )
            )
        s.add_all(buckets)
        for bucket in (old_day, recent_day):
            s.add(
                AggDistinct(
                    tenant_id=tenant_id,
                    key_kind=bucket.key_kind,
                    key=bucket.key,
                    agg_id=bucket.agg_id,
                    bucket_start=bucket.bucket_start,
                    member_hash=b"\x01" * 32,
                )
            )
        for period_key, start, end, status in (
            ("2026-01", date(2026, 1, 1), date(2026, 1, 31), CapturePeriodStatus.OPEN),
            (
                "2025-12",
                date(2025, 12, 1),
                date(2025, 12, 31),
                CapturePeriodStatus.CLOSED,
            ),
        ):
            s.add(
                CapturePeriod(
                    tenant_id=tenant_id,
                    capture_code="ITF_ROR_CASH",
                    report_code="MX_F41_ROR",
                    period_key=period_key,
                    period_start=start,
                    period_end=end,
                    status=status,
                )
            )

    with tenant_session(system_ctx(tenant_id)) as s:
        assert prune_buckets(s, now=NOW) == 3
        left = set(s.scalars(select(AggBucket.key)))
        members = set(s.scalars(select(AggDistinct.key)))
    assert left == {recent_day.key, open_accum.key, open_month.key}
    assert members == {recent_day.key}


def test_a_tenant_without_a_policy_retires_nothing(
    frozen: datetime,
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    old_partitions: None,
    admin_engine: Engine,
):
    _ = Seeder(tenant, queue).month(OLD)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert retention_period(s, today=NOW.date(), since=OLD) is None
    run_retention(tenant)
    assert count(tenant, Transaction) == 2
    assert count(tenant, RuleHit) == 1
    assert len(partitions_left(admin_engine, OLD)) == 2
    # The run is audited all the same, with no period.
    assert audits(tenant) == [
        {
            "period": None,
            "retired": [],
            "skipped": [],
            "counterparties_purged": 0,
            "buckets_pruned": 0,
        }
    ]


def test_the_job_runs_through_the_schedule(
    frozen: datetime,
    itf: CreatedTenant,
    queue: PostgresJobQueue,
    old_partitions: None,
):
    _ = Seeder(itf, queue).month(OLD)
    with tenant_session(system_ctx(itf.tenant_id)) as s:
        schedule = s.scalars(
            select(JobSchedule).where(JobSchedule.name == RETENTION_KIND)
        ).one()
        assert (schedule.kind, schedule.cron, schedule.tz, schedule.payload) == (
            RETENTION_KIND,
            "0 5 1 * *",
            "UTC",
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
    assert count(itf, Transaction) == 0
    assert len(audits(itf)) == 1


# Every foreign key into a table the job deletes from, and how the job clears it.
# A new reference fails this test until the job handles it.
REFERENCES = {
    ("capture_group", "fk_capture_group_superseded_by", "capture_group"),
    (
        "reportable_event",
        "fk_reportable_event_tenant_id_capture_group_id_capture_group",
        "capture_group",
    ),
    (
        "counterparty_name_index",
        "fk_counterparty_name_index_counterparty",
        "counterparty",
    ),
    ("screening_subject", "fk_screening_subject_counterparty", "counterparty"),
    (
        "transaction",
        "fk_transaction_tenant_id_counterparty_id_counterparty",
        "counterparty",
    ),
    ("match_disposition", "fk_match_disposition_prior", "match_disposition"),
    ("screening_whitelist", "fk_screening_whitelist_disposition", "match_disposition"),
    ("reportable_event", "fk_reportable_event_supersedes", "reportable_event"),
    ("reportable_event", "fk_reportable_event_superseded_by", "reportable_event"),
    (
        "rule_hit_transaction",
        "fk_rule_hit_transaction_tenant_id_hit_id_rule_hit",
        "rule_hit",
    ),
    (
        "match_disposition",
        "fk_match_disposition_tenant_id_match_id_screening_match",
        "screening_match",
    ),
    (
        "screening_hit",
        "fk_screening_hit_tenant_id_match_id_screening_match",
        "screening_match",
    ),
    ("screening_match", "fk_screening_match_superseded_by", "screening_match"),
    (
        "screening_hit",
        "fk_screening_hit_tenant_id_run_id_screening_run",
        "screening_run",
    ),
    (
        "screening_match",
        "fk_screening_match_tenant_id_origin_run_id_screening_run",
        "screening_run",
    ),
    ("counterparty", "fk_counterparty_subject", "screening_subject"),
    (
        "screening_match",
        "fk_screening_match_tenant_id_subject_id_screening_subject",
        "screening_subject",
    ),
    (
        "screening_run",
        "fk_screening_run_tenant_id_subject_id_screening_subject",
        "screening_subject",
    ),
    (
        "screening_whitelist",
        "fk_screening_whitelist_tenant_id_subject_id_screening_subject",
        "screening_subject",
    ),
    ("reportable_event", "fk_reportable_event_transaction", "transaction"),
    ("rule_hit", "fk_rule_hit_first_transaction", "transaction"),
    ("rule_hit_transaction", "fk_rule_hit_transaction_txn", "transaction"),
    ("transaction_parked", "fk_transaction_parked_transaction", "transaction"),
    ("transaction_ref", "fk_transaction_ref_transaction", "transaction"),
    ("txn_decision", "fk_txn_decision_transaction", "transaction"),
    ("transaction", "fk_transaction_decision", "txn_decision"),
}
DELETED_FROM = sorted({target for _, _, target in REFERENCES} | {"screening_hit"})


def test_every_reference_into_a_retired_table_is_handled(admin_engine: Engine):
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT conrelid::regclass::text, conname, confrelid::regclass::text
                FROM pg_constraint
                WHERE contype = 'f' AND conparentid = 0
                  AND confrelid::regclass::text = ANY(:targets)
                  AND conrelid::regclass::text !~ '_(y[0-9]{4}m[0-9]{2}|default)$'
                """
            ),
            {"targets": DELETED_FROM},
        ).all()
    assert {(r[0], r[1], r[2]) for r in rows} == REFERENCES


def test_an_ended_assignment_keeps_its_products_months_for_its_own_period(
    frozen: datetime,
    tenant: CreatedTenant,
    packs: dict[str, UUID],
    queue: PostgresJobQueue,
    old_partitions: None,
):
    # ITF (ten years) covered the product from 2016 to 2020; AVI (five years) covers
    # every product today. The 2019 month is past five years, not past ten.
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        assignment_id = assign_pack(
            s,
            pack_version_id=packs[ITF],
            product="EMONEY",
            effective_from=date(2016, 1, 1),
            approved_by=tenant.admin_user_id,
        )
        _ = s.execute(
            update(TenantPackAssignment)
            .where(TenantPackAssignment.id == assignment_id)
            .values(effective_to=date(2020, 12, 31))
        )
    _assign(tenant, packs[AVI], None)
    _ = Seeder(tenant, queue).month(MID)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert retention_period(s, today=NOW.date(), since=MID) == "P10Y"
        # An assignment that ended before the oldest month no longer counts.
        assert retention_period(s, today=NOW.date(), since=date(2021, 1, 1)) == "P5Y"
    run_retention(tenant)
    assert count(tenant, Transaction) == 2
    assert count(tenant, RuleHit) == 1
    assert audits(tenant)[0]["period"] == "P10Y"


def test_a_hold_placed_during_the_run_stops_the_later_steps(
    frozen: datetime,
    itf: CreatedTenant,
    queue: PostgresJobQueue,
    old_partitions: None,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
):
    seeder = Seeder(itf, queue)
    _ = seeder.month(OLD)
    real = retention.held_parties_in
    calls: list[date] = []

    def hold_after_the_first_step(s: Session, month: date) -> int:
        calls.append(month)
        if len(calls) == 2:
            seeder.hold()  # the MLRO, between the first and the second step
        return real(s, month)

    monkeypatch.setattr(
        f"{retention.__name__}.held_parties_in", hold_after_the_first_step
    )
    run_retention(itf)
    # The first step ran before the hold existed; nothing after it did.
    assert count(itf, RuleHitTransaction) == 0
    assert count(itf, RuleHit) == 1
    assert count(itf, ReportableEvent) == 2
    assert count(itf, TxnDecision) == 1
    assert count(itf, Transaction) == 2
    assert len(partitions_left(admin_engine, OLD)) == 2
    (detail,) = audits(itf)
    assert detail["skipped"] == ["2016-11"]
    assert [m["hit_links"] for m in detail["retired"]] == [2]


def test_the_purge_skips_a_counterparty_whose_subject_is_busy(
    frozen: datetime,
    itf: CreatedTenant,
    queue: PostgresJobQueue,
    old_partitions: None,
    admin_engine: Engine,
):
    seeder = Seeder(itf, queue)
    gone = seeder.txn(_at(OLD, 5), counterparty="Ana Pérez Gómez")
    assert gone.counterparty_id is not None
    ids = _screen(seeder, gone.counterparty_id, decided_at=_at(OLD, 6))
    with admin_engine.connect() as screener:
        busy = screener.begin()
        _ = screener.execute(
            text("SELECT 1 FROM screening_subject WHERE id = :id FOR UPDATE"),
            {"id": ids["subject"]},
        )
        run_retention(itf)  # does not wait for the screening job
        busy.rollback()
    assert count(itf, Counterparty, Counterparty.id == gone.counterparty_id) == 1
    assert audits(itf)[0]["counterparties_purged"] == 0
    run_retention(itf)
    assert count(itf, Counterparty, Counterparty.id == gone.counterparty_id) == 0
    assert audits(itf)[1]["counterparties_purged"] == 1


def test_the_hold_lock_stops_a_hold_being_set_and_lets_edits_through(
    frozen: datetime,
    itf: CreatedTenant,
    queue: PostgresJobQueue,
    old_partitions: None,
):
    # The step's FOR KEY SHARE conflicts with set_legal_hold's FOR UPDATE, not with
    # an ordinary edit of the party.
    seeder = Seeder(itf, queue)
    _ = seeder.month(OLD)
    with tenant_session(system_ctx(itf.tenant_id)) as step:
        assert retention.held_parties_in(step, OLD) == 0  # takes the locks
        with staff_session(itf.tenant_id, itf.admin_user_id) as edit:
            _ = edit.execute(text("SET LOCAL lock_timeout = '200ms'"))
            _ = edit.execute(
                update(Party).where(Party.id == seeder.party_id).values(updated_at=NOW)
            )
        with (
            pytest.raises(DBAPIError) as caught,
            staff_session(itf.tenant_id, itf.admin_user_id) as hold,
        ):
            _ = hold.execute(text("SET LOCAL lock_timeout = '200ms'"))
            _ = set_legal_hold(hold, seeder.party_id, legal_hold=True)
    assert getattr(caught.value.orig, "sqlstate", None) == "55P03"


def test_a_held_partys_rows_are_never_deleted_even_unlocked(
    frozen: datetime,
    itf: CreatedTenant,
    queue: PostgresJobQueue,
    old_partitions: None,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
):
    # A row that reaches the month after the step locked its parties (a BACKFILL
    # row of an already held party) is still kept by the deletes themselves.
    seeder = Seeder(itf, queue)
    _ = seeder.month(OLD)
    seeder.hold()

    def nobody_held(_s: Session, _month: date) -> int:
        return 0

    monkeypatch.setattr(f"{retention.__name__}.held_parties_in", nobody_held)
    run_retention(itf)
    assert count(itf, Transaction) == 2
    assert count(itf, TransactionRef) == 2
    assert count(itf, TransactionParked) == 1
    assert count(itf, RuleHit) == 1
    assert count(itf, RuleHitTransaction) == 2
    assert count(itf, ReportableEvent) == 2
    assert count(itf, TxnDecision) == 1
    assert len(partitions_left(admin_engine, OLD)) == 2
