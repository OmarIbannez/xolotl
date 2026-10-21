"""Enrichment: the snapshot, unit amounts, periods, counterparty and device (A6)."""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from tests.helpers import everything_stored, new_tenant, staff_session
from tests.modules.monitoring.support import FROM, ITF, both_packs_tenant, itf_tenant
from xolotl.modules.monitoring.enrich.policy import (
    SnapshotCache,
    SnapshotRef,
    policy_date,
    snapshot_for,
)
from xolotl.modules.monitoring.enrich.service import (
    ParkTransaction,
    apply_enrichment,
    enrich,
    enrich_input,
    enriched_from_row,
)
from xolotl.modules.monitoring.enrich.units import unit_pairs
from xolotl.modules.monitoring.enums import (
    AccountKind,
    BatchSource,
    ParkReason,
    TransactionStatus,
)
from xolotl.modules.monitoring.txn.models import Account, Transaction, TransactionBatch
from xolotl.modules.monitoring.txn.schema import (
    EnrichedTransaction,
    TransactionInput,
    UnitAmount,
    amount_minor,
)
from xolotl.modules.party.enums import CustomerRelationshipStatus, PartyType
from xolotl.modules.party.models import CustomerRelationship, Party
from xolotl.modules.party.service import RelationshipRef
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.resolve import dedup_hash_of
from xolotl.modules.screening.counterparty.schema import CounterpartyScreeningStatus
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.calendar import Holidays, load_holidays
from xolotl.platform.crypto import encrypt_json
from xolotl.platform.crypto.blind_index import blind_index
from xolotl.platform.enums import HolidayScope
from xolotl.platform.policy import (
    PolicyNotAssignedError,
    PolicyPack,
    PolicyPackVersion,
    assign_pack,
)
from xolotl.platform.policy.snapshots import snapshot_policy
from xolotl.platform.units import ConversionRule, UnitService

# 2026-01-20 has every unit value in tests/golden/units/values.yaml.
ON = datetime(2026, 1, 20, 18, 0, tzinfo=UTC)
IP = "203.0.113.9"
DEVICE = "device-abc-123"


@contextmanager
def _session(tenant: CreatedTenant) -> Generator[Session]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        yield s


def _holidays(s: Session) -> Holidays:
    return load_holidays(s, country="MX", scope=HolidayScope.BANXICO)


def _item(**overrides: Any) -> TransactionInput:
    data: dict[str, Any] = {
        "external_id": "ext-1",
        "occurred_at": ON.isoformat(),
        "customer_ref": "cust-1",
        "direction": "IN",
        "rail": "SPEI",
        "instrument": "TRANSFER",
        "amount": "10000.00",
        "currency": "MXN",
        "counterparty": {
            "kind": "PERSON",
            "name": "Juan Pérez",
            "account": "012180001234567891",
            "country": "MX",
        },
        "device": {"device_id": DEVICE, "ip": IP, "geo_country": "MX"},
    }
    data.update(overrides)
    return TransactionInput.model_validate(data)


def _relationship(
    s: Session, tenant_id: UUID, *, product: str = "EMONEY", tier: str = "N1"
) -> RelationshipRef:
    party = Party(tenant_id=tenant_id, party_type=PartyType.PERSON, country_code="MX")
    s.add(party)
    s.flush()
    row = CustomerRelationship(
        tenant_id=tenant_id,
        party_id=party.id,
        external_ref=f"cust-{party.id.hex[:8]}",
        status=CustomerRelationshipStatus.ACTIVE,
        product=product,
        account_tier=tier,
    )
    s.add(row)
    s.flush()
    return RelationshipRef(
        id=row.id,
        party_id=row.party_id,
        product=product,
        account_tier=tier,
        status=row.status,
    )


def _counterparty(s: Session, tenant_id: UUID, item: TransactionInput) -> UUID:
    assert item.counterparty is not None
    row = Counterparty(
        tenant_id=tenant_id,
        kind=item.counterparty.kind,
        account_hash=b"\x02" * 32,
        dedup_hash=dedup_hash_of(s, item.counterparty),
        country=item.counterparty.country,
    )
    s.add(row)
    s.flush()
    return row.id


def _store(
    s: Session, tenant_id: UUID, item: TransactionInput, relationship: RelationshipRef
) -> Transaction:
    """Write the row A4's ingest would write for ``item``.

    Returns:
        The flushed row.

    """
    account = Account(
        tenant_id=tenant_id,
        relationship_id=relationship.id,
        party_id=relationship.party_id,
        external_ref=f"default:{relationship.id}",
        kind=AccountKind.DEFAULT,
    )
    batch = TransactionBatch(
        tenant_id=tenant_id,
        source=BatchSource.API,
        submitted_by="cli:test",
        item_count=1,
        accepted=1,
        duplicates=0,
    )
    s.add_all([account, batch])
    s.flush()
    device_id = None if item.device is None else item.device.device_id
    row = Transaction(
        tenant_id=tenant_id,
        occurred_at=item.occurred_at,
        external_id=item.external_id,
        received_at=ON,
        batch_id=batch.id,
        party_id=relationship.party_id,
        relationship_id=relationship.id,
        account_id=account.id,
        product=relationship.product,
        direction=item.direction,
        rail=item.rail,
        instrument=item.instrument,
        channel=item.channel,
        amount_minor=amount_minor(item.amount),
        currency=item.currency,
        counterparty_id=(
            None if item.counterparty is None else _counterparty(s, tenant_id, item)
        ),
        device_hash=(
            None
            if device_id is None
            else blind_index("device", device_id, tenant_id=tenant_id, session=s)
        ),
        geo_country=None if item.device is None else item.device.geo_country,
        content_hash=b"\x00" * 32,
        raw=encrypt_json(item.model_dump(mode="json"), tenant_id=tenant_id, session=s),
    )
    s.add(row)
    s.flush()
    return row


def _enrich(
    s: Session, row: Transaction, relationship: RelationshipRef
) -> EnrichedTransaction:
    snapshot = snapshot_for(
        s, on=policy_date(row.occurred_at), product=relationship.product, cache={}
    )
    return enrich(
        s,
        row,
        units=UnitService(s),
        holidays=_holidays(s),
        relationship=relationship,
        snapshot=snapshot,
    )


def _snapshot(s: Session, product: str | None) -> SnapshotRef:
    return snapshot_for(s, on=ON.date(), product=product, cache={})


def test_unit_pairs_come_from_captures_tiers_and_the_library_pair(
    tenant: CreatedTenant, admin_engine: Engine
):
    _ = both_packs_tenant(tenant, admin_engine)
    with _session(tenant) as s:
        itf = unit_pairs(_snapshot(s, "EMONEY").policy)
        # The AVI pack is in force from 2026-11-30.
        crypto = snapshot_for(s, on=date(2026, 12, 1), product="CRYPTO", cache={})
        avi = unit_pairs(crypto.policy)
    assert itf == {
        ("USD", ConversionRule.PRIOR_BANKING_DAY),
        ("UDI", ConversionRule.SAME_DAY),
        ("UDI", ConversionRule.LAST_DAY_PREV_MONTH),
    }
    assert avi == {
        ("USD", ConversionRule.PRIOR_BANKING_DAY),
        ("UMA", ConversionRule.SAME_DAY),
    }


def test_a_mxn_transaction_records_the_local_leg_without_a_row(
    tenant: CreatedTenant, admin_engine: Engine
):
    _ = itf_tenant(tenant, admin_engine)
    with _session(tenant) as s:
        relationship = _relationship(s, tenant.tenant_id)
        txn = _enrich(
            s, _store(s, tenant.tenant_id, _item(), relationship), relationship
        )
    assert txn.unit_amounts["MXN/SAME_DAY"] == UnitAmount(
        amount=Decimal("10000.00"), unit_value_id=None, resolved_date=ON.date()
    )
    assert set(txn.unit_amounts) == {
        "MXN/SAME_DAY",
        "UDI/SAME_DAY",
        "UDI/LAST_DAY_PREV_MONTH",
        "USD/PRIOR_BANKING_DAY",
    }
    # 10,000 / 8.61 (UDI of 2026-01-20) and / 18.50 (FIX of 2026-01-19).
    assert txn.unit_amounts["UDI/SAME_DAY"].amount == Decimal("1161.440186")
    assert txn.unit_amounts["USD/PRIOR_BANKING_DAY"].resolved_date == date(2026, 1, 19)
    assert txn.unit_amounts["USD/PRIOR_BANKING_DAY"].amount == Decimal("540.540541")
    assert txn.tier == "N1"
    assert txn.risk_rating is None
    assert txn.monitoring_profile == "STD"
    assert txn.policy_country == "MX"


def test_a_usd_transaction_converts_through_the_fix_first(
    tenant: CreatedTenant, admin_engine: Engine
):
    _ = itf_tenant(tenant, admin_engine)
    with _session(tenant) as s:
        relationship = _relationship(s, tenant.tenant_id)
        item = _item(amount="1000.00", currency="USD")
        txn = _enrich(s, _store(s, tenant.tenant_id, item, relationship), relationship)
    local = txn.unit_amounts["MXN/PRIOR_BANKING_DAY"]
    assert local.amount == Decimal("18500.00")
    assert local.resolved_date == date(2026, 1, 19)
    assert local.unit_value_id is not None
    assert "MXN/SAME_DAY" not in txn.unit_amounts
    assert txn.unit_amounts["USD/PRIOR_BANKING_DAY"] == UnitAmount(
        amount=Decimal("1000.00"), unit_value_id=None, resolved_date=ON.date()
    )
    # The pesos, not the dollars, are what the UDI conversion reads.
    assert txn.unit_amounts["UDI/SAME_DAY"].amount == Decimal("2148.664344")


def test_the_snapshot_is_memoised_per_date_and_product(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    _ = itf_tenant(tenant, admin_engine)
    calls: list[str] = []

    def spy(session: Session, effective: Any) -> UUID:
        calls.append("snapshot")
        return snapshot_policy(session, effective)

    monkeypatch.setattr("xolotl.modules.monitoring.enrich.policy.snapshot_policy", spy)
    cache: SnapshotCache = {}
    with _session(tenant) as s:
        first = snapshot_for(s, on=ON.date(), product="EMONEY", cache=cache)
        second = snapshot_for(s, on=ON.date(), product="EMONEY", cache=cache)
        other = snapshot_for(s, on=date(2026, 1, 21), product="EMONEY", cache=cache)
    assert first is second
    assert other is not first
    assert calls == ["snapshot", "snapshot"]


def test_no_policy_on_the_date_parks_with_policy_not_assigned(
    tenant: CreatedTenant, admin_engine: Engine
):
    _ = itf_tenant(tenant, admin_engine)
    with _session(tenant) as s, pytest.raises(PolicyNotAssignedError):
        _ = snapshot_for(s, on=date(2025, 12, 31), product="EMONEY", cache={})


def test_a_missing_unit_value_parks_the_transaction(
    tenant: CreatedTenant, admin_engine: Engine
):
    _ = itf_tenant(tenant, admin_engine)
    with _session(tenant) as s:
        relationship = _relationship(s, tenant.tenant_id)
        # 2026-01-22 has no UDI row: SAME_DAY is the first missing pair.
        item = _item(occurred_at="2026-01-22T18:00:00Z")
        row = _store(s, tenant.tenant_id, item, relationship)
        with pytest.raises(ParkTransaction) as caught:
            _ = _enrich(s, row, relationship)
    assert caught.value.reason is ParkReason.UNIT_VALUE_MISSING
    assert caught.value.unit_code == "UDI"
    assert caught.value.needed_on == date(2026, 1, 22)


def test_a_missing_fix_parks_a_usd_transaction_on_the_fix(
    tenant: CreatedTenant, admin_engine: Engine
):
    _ = itf_tenant(tenant, admin_engine)
    with _session(tenant) as s:
        relationship = _relationship(s, tenant.tenant_id)
        # The FIX of 2026-01-20, which the 21st reads, is not in the fixture.
        item = _item(occurred_at="2026-01-21T18:00:00Z", currency="USD")
        row = _store(s, tenant.tenant_id, item, relationship)
        with pytest.raises(ParkTransaction) as caught:
            _ = _enrich(s, row, relationship)
    assert caught.value.unit_code == "USD_MXN_FIX"
    assert caught.value.needed_on == date(2026, 1, 20)


def test_the_counterparty_status_and_country_are_read_at_enrichment(
    tenant: CreatedTenant, admin_engine: Engine
):
    _ = itf_tenant(tenant, admin_engine)
    with _session(tenant) as s:
        relationship = _relationship(s, tenant.tenant_id)
        item = _item()
        row = _store(s, tenant.tenant_id, item, relationship)
        before = _enrich(s, row, relationship)
        _ = s.execute(
            update(Counterparty)
            .where(Counterparty.id == row.counterparty_id)
            .values(screening_status=CounterpartyScreeningStatus.TRUE_MATCH)
        )
        after = _enrich(s, row, relationship)
        assert item.counterparty is not None
        expected_key = dedup_hash_of(s, item.counterparty).hex()
    assert (
        before.counterparty_screening_status is CounterpartyScreeningStatus.UNSCREENED
    )
    assert after.counterparty_screening_status is CounterpartyScreeningStatus.TRUE_MATCH
    assert after.counterparty_country == "MX"
    assert after.counterparty_id == row.counterparty_id
    assert after.counterparty_key == expected_key


def test_a_transaction_without_counterparty_or_device_has_no_keys(
    tenant: CreatedTenant, admin_engine: Engine
):
    _ = itf_tenant(tenant, admin_engine)
    with _session(tenant) as s:
        relationship = _relationship(s, tenant.tenant_id)
        item = _item(counterparty=None, device=None)
        txn = _enrich(s, _store(s, tenant.tenant_id, item, relationship), relationship)
    assert txn.counterparty_key is None
    assert txn.counterparty_screening_status is None
    assert txn.device_key is None


def test_the_device_hash_is_keyed_per_tenant_and_the_ip_is_not_stored(
    tenant: CreatedTenant, admin_engine: Engine
):
    _ = itf_tenant(tenant, admin_engine)
    other = new_tenant("otra", "admin@otra.mx")
    with admin_engine.connect() as conn:
        itf_version = conn.execute(
            select(PolicyPackVersion.id)
            .join(PolicyPack, PolicyPack.id == PolicyPackVersion.pack_id)
            .where(PolicyPack.code == ITF)
        ).scalar_one()
    with _session(other) as s:
        _ = assign_pack(
            s,
            pack_version_id=itf_version,
            product=None,
            effective_from=FROM,
            approved_by=other.admin_user_id,
        )
    keys: list[str | None] = []
    for owner in (tenant, other):
        with _session(owner) as s:
            relationship = _relationship(s, owner.tenant_id)
            row = _store(s, owner.tenant_id, _item(), relationship)
            txn = _enrich(s, row, relationship)
            apply_enrichment(row, txn)
            keys.append(txn.device_key)
            assert IP not in txn.model_dump_json()
    assert keys[0] is not None
    assert keys[1] is not None
    assert keys[0] != keys[1]
    stored = everything_stored(admin_engine)
    assert IP not in stored
    assert DEVICE not in stored


def test_enriched_from_row_rebuilds_what_apply_enrichment_wrote(
    tenant: CreatedTenant, admin_engine: Engine
):
    _ = itf_tenant(tenant, admin_engine)
    with _session(tenant) as s:
        relationship = _relationship(s, tenant.tenant_id)
        row = _store(s, tenant.tenant_id, _item(), relationship)
        row.late = True
        txn = _enrich(s, row, relationship)
        apply_enrichment(row, txn)
        row.status = TransactionStatus.PROCESSED
        s.flush()
        s.expire(row)
        loaded = s.scalars(select(Transaction).where(Transaction.id == row.id)).one()
        rebuilt = enriched_from_row(loaded)
    assert rebuilt == txn
    assert rebuilt.late is True
    assert rebuilt.received_at == ON
    assert rebuilt.counterparty_key is not None


def test_enriched_from_row_refuses_a_row_never_enriched(
    tenant: CreatedTenant, admin_engine: Engine
):
    _ = itf_tenant(tenant, admin_engine)
    with _session(tenant) as s:
        relationship = _relationship(s, tenant.tenant_id)
        row = _store(s, tenant.tenant_id, _item(), relationship)
        with pytest.raises(ValueError, match="not been enriched"):
            _ = enriched_from_row(row)


def test_enrich_input_gives_the_same_unit_amounts_and_periods_as_enrich(
    tenant: CreatedTenant, admin_engine: Engine
):
    _ = itf_tenant(tenant, admin_engine)
    now = datetime(2026, 1, 20, 18, 5, tzinfo=UTC)
    with _session(tenant) as s:
        relationship = _relationship(s, tenant.tenant_id)
        stored = _item()
        row = _store(s, tenant.tenant_id, stored, relationship)
        from_row = _enrich(s, row, relationship)
        counterparties = s.scalars(select(Counterparty.id)).all()
        candidate = _item(
            external_id="ext-2",
            counterparty={"kind": "PERSON", "name": "Ana López", "country": "US"},
        )
        decided = enrich_input(
            s,
            candidate,
            relationship=relationship,
            account_id=None,
            units=UnitService(s),
            holidays=_holidays(s),
            snapshot=_snapshot(s, "EMONEY"),
            now=now,
        )
        same = enrich_input(
            s,
            stored,
            relationship=relationship,
            account_id=row.account_id,
            units=UnitService(s),
            holidays=_holidays(s),
            snapshot=_snapshot(s, "EMONEY"),
            now=now,
        )
        assert s.scalars(select(Counterparty.id)).all() == counterparties
    assert decided.unit_amounts == from_row.unit_amounts
    assert decided.period_keys == from_row.period_keys
    assert decided.account_id is None
    assert decided.counterparty_id is None
    assert (
        decided.counterparty_screening_status is CounterpartyScreeningStatus.UNSCREENED
    )
    assert decided.counterparty_country == "US"
    assert decided.received_at == now
    assert decided.late is False
    assert decided.id != from_row.id
    # Over the stored item the decide path finds the same counterparty and device.
    assert same.model_dump(
        exclude={"id", "received_at", "counterparty_id", "late"}
    ) == from_row.model_dump(exclude={"id", "received_at", "counterparty_id", "late"})


def test_the_counterparty_key_covers_every_component(
    tenant: CreatedTenant, admin_engine: Engine
):
    _ = itf_tenant(tenant, admin_engine)
    bodies: list[dict[str, Any]] = [
        {"name": "Juan Pérez"},
        {"name": "juan perez "},
        {"wallet_address": "0xAbC"},
        {"wallet_address": "0xabc"},
        {"identifier": {"id_type": "RFC", "value": "GOMA800101H7A"}},
    ]
    with _session(tenant) as s:
        relationship = _relationship(s, tenant.tenant_id)
        keys = [
            enrich_input(
                s,
                _item(counterparty=body, device={"device_id": " "}),
                relationship=relationship,
                account_id=None,
                units=UnitService(s),
                holidays=_holidays(s),
                snapshot=_snapshot(s, "EMONEY"),
                now=ON,
            )
            for body in bodies
        ]
    assert keys[0].counterparty_key == keys[1].counterparty_key
    assert len({txn.counterparty_key for txn in keys}) == 4
    assert keys[0].device_key is None
