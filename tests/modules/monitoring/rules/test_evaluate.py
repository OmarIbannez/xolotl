"""The processing side of the evaluator: the aggregate reader and the profile (B7)."""

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import update

from tests.helpers import staff_session, system_ctx
from tests.modules.monitoring.rules.conftest import OCCURRED, enriched
from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    Measure,
    WindowKind,
)
from xolotl.modules.monitoring.aggregates.store import (
    EMPTY,
    AggregateKey,
    AggregateValue,
)
from xolotl.modules.monitoring.rules.evaluate import (
    months_between,
    profile_for,
    reader_for,
    use_screening_status,
)
from xolotl.modules.monitoring.rules.kinds import INCOMPLETE
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction, KeyKind
from xolotl.modules.party.enums import PepStatus
from xolotl.modules.party.models import CustomerRelationship, Person
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session


def _declaration(
    name: str, window_kind: WindowKind, window: str, *, key_kind: KeyKind
) -> AggregateDeclaration:
    return AggregateDeclaration(
        name=name,
        function=AggregateFunction.COUNT,
        measure=Measure.ONE,
        unit_key=None,
        key_kind=key_kind,
        window_kind=window_kind,
        window=window,
    )


DURATION = _declaration(
    "p7d", WindowKind.DURATION, "P7D", key_kind=KeyKind.RELATIONSHIP
)
MONTH = _declaration("mtd", WindowKind.MONTH, "MONTH", key_kind=KeyKind.PARTY)
QUARTER = _declaration("qtd", WindowKind.QUARTER, "QUARTER", key_kind=KeyKind.PARTY)
ACCUM = _declaration("acc", WindowKind.ACCUM, "P6M", key_kind=KeyKind.ACCOUNT)
DEVICE = _declaration("dev", WindowKind.DURATION, "P1D", key_kind=KeyKind.DEVICE)
PARTIAL = _declaration(
    "partial", WindowKind.DURATION, "P1D", key_kind=KeyKind.RELATIONSHIP
)


def _value(count: int, *, complete: bool = True) -> AggregateValue:
    return AggregateValue(
        Decimal(count), count, None, None, None, None, None, complete=complete
    )


class RecordingStore:
    """Answers every read with a count of one and records which read was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, AggregateKey, str, object]] = []

    def add(
        self, txn: EnrichedTransaction, declarations: Sequence[AggregateDeclaration]
    ) -> None:
        raise NotImplementedError

    def read(
        self, key: AggregateKey, declaration: AggregateDeclaration, *, at: datetime
    ) -> AggregateValue:
        self.calls.append(("read", key, declaration.name, at))
        return _value(1, complete=declaration.name != PARTIAL.name)

    def calendar_read(
        self, key: AggregateKey, declaration: AggregateDeclaration, *, period_key: str
    ) -> AggregateValue:
        self.calls.append(("calendar_read", key, declaration.name, period_key))
        return _value(1)

    def accumulator_read(
        self, key: AggregateKey, declaration: AggregateDeclaration, *, at: datetime
    ) -> AggregateValue:
        self.calls.append(("accumulator_read", key, declaration.name, at))
        return _value(1)


def test_the_reader_picks_the_read_by_window_kind():
    txn = enriched()
    store = RecordingStore()
    declared = (DURATION, MONTH, QUARTER, ACCUM, DEVICE, PARTIAL)
    reader = reader_for(store, txn, {item.name: item for item in declared})

    for name in ("p7d", "mtd", "qtd", "acc"):
        assert reader.read(name) == _value(1)
    relationship = AggregateKey(KeyKind.RELATIONSHIP, str(txn.relationship_id))
    party = AggregateKey(KeyKind.PARTY, str(txn.party_id))
    account = AggregateKey(KeyKind.ACCOUNT, str(txn.account_id))
    assert store.calls == [
        ("read", relationship, "p7d", OCCURRED),
        ("calendar_read", party, "mtd", "2026-12"),
        ("calendar_read", party, "qtd", "2026-Q4"),
        ("accumulator_read", account, "acc", OCCURRED),
    ]

    # Reads are memoised per name and instant; another instant reads again, and a
    # calendar read derives its period from that instant.
    assert reader.read("p7d") == _value(1)
    assert len(store.calls) == 4
    before = datetime(2026, 9, 30, 12, tzinfo=UTC)
    _ = reader.read("p7d", at=before)
    _ = reader.read("mtd", at=before)
    _ = reader.read("qtd", at=before)
    assert store.calls[4:] == [
        ("read", relationship, "p7d", before),
        ("calendar_read", party, "mtd", "2026-09"),
        ("calendar_read", party, "qtd", "2026-Q3"),
    ]

    # No device on the transaction: nothing counted. An unbackfilled declaration
    # and an undeclared name read as INCOMPLETE.
    assert reader.read("dev") == EMPTY
    assert reader.read("partial") is INCOMPLETE
    assert reader.read("undeclared") is INCOMPLETE


def test_the_reader_reads_the_mexico_city_month_of_an_instant():
    # 1 December 03:00 UTC is still 30 November in Mexico City.
    txn = enriched()
    store = RecordingStore()
    reader = reader_for(store, txn, {MONTH.name: MONTH})
    _ = reader.read("mtd", at=datetime(2026, 12, 1, 3, tzinfo=UTC))
    assert store.calls[0][3] == "2026-11"


@pytest.fixture
def no_screening_reader() -> Iterator[None]:
    use_screening_status(None)
    yield
    use_screening_status(None)


@pytest.mark.usefixtures("no_screening_reader")
def test_the_profile_reads_tier_pep_and_screening_status(
    tenant: CreatedTenant, customer: CustomerRelationship
):
    started = datetime(2025, 3, 1, tzinfo=UTC)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = s.execute(
            update(Person)
            .where(Person.id == customer.party_id)
            .values(pep_status=PepStatus.DOMESTIC)
        )
        _ = s.execute(
            update(CustomerRelationship)
            .where(CustomerRelationship.id == customer.id)
            .values(started_at=started)
        )
    txn = enriched(
        party_id=customer.party_id,
        relationship_id=customer.id,
        occurred_at=OCCURRED,
    )

    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        profile = profile_for(s, txn)
    assert profile.tier == "N1"
    assert profile.pep_status == "DOMESTIC"
    assert profile.party_screening_status is None
    assert profile.declared_profile is None
    assert profile.relationship_started_at == started
    assert profile.history_months == 0
    assert (profile.risk_rating, profile.monitoring_profile) == (
        txn.risk_rating,
        txn.monitoring_profile,
    )

    # The screening status comes from the reader the composition root installs.
    asked: list[UUID] = []

    def clear(_s: object, party_id: UUID) -> str:
        asked.append(party_id)
        return "CLEAR"

    use_screening_status(clear)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert profile_for(s, txn).party_screening_status == "CLEAR"
    assert asked == [customer.party_id]


def test_history_months_count_whole_months_since_the_first_processed_row():
    first = datetime(2026, 6, 15, 12, tzinfo=UTC)
    assert months_between(first, first) == 0
    assert months_between(first, first + timedelta(days=20)) == 0
    assert months_between(first, datetime(2026, 7, 15, 12, tzinfo=UTC)) == 1
    assert months_between(first, datetime(2026, 9, 14, 12, tzinfo=UTC)) == 2
    assert months_between(first, datetime(2026, 12, 20, tzinfo=UTC)) == 6
