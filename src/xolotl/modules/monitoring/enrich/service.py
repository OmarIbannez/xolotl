"""Enrich a transaction for the aggregates and the evaluator (SP-04 spec §7).

``enrich`` runs inside the processing transaction over a stored row: it reads
the counterparty's country and screening status as they are now, converts the
amount into every unit of the snapshot's policy and computes the period keys.
``enrich_input`` does the same over a validated item that has no row (the decide
path, reconciliation finding 10): the counterparty is found by its key, never
written. ``apply_enrichment`` stores the result on the row and
``enriched_from_row`` rebuilds it from a processed row without converting again.
A unit value that is not there yet raises ``ParkTransactionError``; a date with no
policy raises ``PolicyNotAssignedError`` from ``snapshot_for``, before this.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.orm import Session, object_session

from xolotl.modules.monitoring.enrich.periods import period_keys_for
from xolotl.modules.monitoring.enrich.policy import SnapshotRef, policy_date
from xolotl.modules.monitoring.enrich.units import (
    ParkTransactionError,
    unit_amounts_for,
    unit_pairs,
)
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import (
    EnrichedTransaction,
    PeriodKeys,
    TransactionInput,
    UnitAmount,
    amount_minor,
    counterparty_of,
)
from xolotl.modules.party.service import RelationshipRef
from xolotl.modules.screening.counterparty.resolve import (
    CounterpartyUnindexableError,
    country_and_status_by_dedup_hash,
    dedup_hash_of,
    status_of,
)
from xolotl.modules.screening.counterparty.schema import CounterpartyScreeningStatus
from xolotl.platform.calendar import Holidays
from xolotl.platform.crypto.blind_index import blind_index, normalize_identifier
from xolotl.platform.db import bound_context
from xolotl.platform.ids import uuid7
from xolotl.platform.policy.models import PolicySnapshot
from xolotl.platform.units import UnitService

__all__ = [
    "ParkTransactionError",
    "apply_enrichment",
    "enrich",
    "enrich_input",
    "enriched_from_row",
]

# Until SP-07 assigns monitoring profiles (program spec §7.2).
STANDARD_PROFILE = "STD"


@dataclass(frozen=True, slots=True)
class _Valued:
    unit_amounts: dict[str, UnitAmount]
    period_keys: PeriodKeys


def _valued(
    units: UnitService,
    *,
    amount: Decimal,
    currency: str,
    occurred_at: datetime,
    snapshot: SnapshotRef,
    holidays: Holidays,
) -> _Valued:
    return _Valued(
        unit_amounts=unit_amounts_for(
            units,
            amount=amount,
            currency=currency,
            on=policy_date(occurred_at),
            pairs=unit_pairs(snapshot.policy),
            holidays=holidays,
        ),
        period_keys=period_keys_for(occurred_at, holidays=holidays),
    )


def _json(value: dict[str, object] | None) -> dict[str, JsonValue] | None:
    return cast("dict[str, JsonValue] | None", value)


def enrich(
    session: Session,
    row: Transaction,
    *,
    units: UnitService,
    holidays: Holidays,
    relationship: RelationshipRef,
    snapshot: SnapshotRef,
) -> EnrichedTransaction:
    """Enrich a stored transaction with its policy, units, periods and context.

    A unit value the policy needs and nobody has loaded yet raises
    ``ParkTransactionError`` from ``unit_amounts_for``.

    Args:
        session: The processing transaction's session.
        row: The transaction; ``late`` is taken as the row carries it.
        units: The unit service of ``session``.
        holidays: Mexico's ``BANXICO`` holidays.
        relationship: The row's relationship (its account tier).
        snapshot: The policy snapshot for the row's date and product.

    Returns:
        The enriched transaction.

    """
    counterparty_key: str | None = None
    country: str | None = None
    status: CounterpartyScreeningStatus | None = None
    if row.counterparty_id is not None:
        dedup_hash, country, status = status_of(session, row.counterparty_id)
        counterparty_key = dedup_hash.hex()
    valued = _valued(
        units,
        amount=Decimal(row.amount_minor).scaleb(-2),
        currency=row.currency,
        occurred_at=row.occurred_at,
        snapshot=snapshot,
        holidays=holidays,
    )
    return EnrichedTransaction(
        id=row.id,
        external_id=row.external_id,
        occurred_at=row.occurred_at,
        received_at=row.received_at,
        party_id=row.party_id,
        relationship_id=row.relationship_id,
        account_id=row.account_id,
        product=row.product,
        direction=row.direction,
        rail=row.rail,
        instrument=row.instrument,
        channel=row.channel,
        amount_minor=row.amount_minor,
        currency=row.currency,
        fee_minor=row.fee_minor,
        asset_qty=row.asset_qty,
        asset_code=row.asset_code,
        unit_amounts=valued.unit_amounts,
        counterparty_id=row.counterparty_id,
        counterparty_key=counterparty_key,
        counterparty_country=country,
        counterparty_screening_status=status,
        device_key=None if row.device_hash is None else row.device_hash.hex(),
        geo_country=row.geo_country,
        geo_admin_area=row.geo_admin_area,
        crypto=_json(row.crypto),
        fx=_json(row.fx),
        purpose_code=row.purpose_code,
        period_keys=valued.period_keys,
        tier=relationship.account_tier,
        risk_rating=None,
        monitoring_profile=STANDARD_PROFILE,
        policy_snapshot_id=snapshot.snapshot_id,
        policy_country=snapshot.policy.country,
        late=row.late,
    )


def _device_key(session: Session, item: TransactionInput) -> str | None:
    device_id = None if item.device is None else item.device.device_id
    if not device_id or not normalize_identifier("device", device_id):
        return None
    tenant_id = bound_context(session).require_tenant_id()
    return blind_index("device", device_id, tenant_id=tenant_id, session=session).hex()


def enrich_input(
    session: Session,
    item: TransactionInput,
    *,
    relationship: RelationshipRef,
    account_id: UUID | None,
    units: UnitService,
    holidays: Holidays,
    snapshot: SnapshotRef,
    now: datetime,
) -> EnrichedTransaction:
    """Enrich a validated item that has no row (the decide path).

    The counterparty is ``counterparty_of(item)``, found by its dedup hash as
    ingest keys it, and nothing is written: a counterparty never seen is
    ``UNSCREENED`` with no id, and one with nothing to index is no counterparty.
    A counterparty already stored carries its stored country, as ``enrich``
    reads it, so the decide and processing paths agree.
    A missing unit value raises ``ParkTransactionError``, as in ``enrich`` (C5 maps it
    to the fail mode).

    Args:
        session: The caller's session.
        item: The validated transaction.
        relationship: The customer's relationship.
        account_id: The account, or None for an ``account_ref`` never seen.
        units: The unit service of ``session``.
        holidays: Mexico's ``BANXICO`` holidays.
        snapshot: The policy snapshot for the item's date and the product.
        now: The receiving instant.

    Returns:
        The enriched transaction with a fresh id, ``received_at = now`` and
        ``late`` false.

    """
    counterparty_key: str | None = None
    country: str | None = None
    status: CounterpartyScreeningStatus | None = None
    counterparty = counterparty_of(item)
    dedup_hash: bytes | None = None
    if counterparty is not None:
        try:
            dedup_hash = dedup_hash_of(session, counterparty)
        except CounterpartyUnindexableError:
            # Ingest stores no counterparty for such an input; neither does this.
            dedup_hash = None
    if counterparty is not None and dedup_hash is not None:
        counterparty_key = dedup_hash.hex()
        stored = country_and_status_by_dedup_hash(session, dedup_hash)
        if stored is None:
            country = counterparty.country
            status = CounterpartyScreeningStatus.UNSCREENED
        else:
            # The stored row's country, as ``enrich`` reads it on processing.
            country, status = stored
    valued = _valued(
        units,
        amount=item.amount,
        currency=item.currency,
        occurred_at=item.occurred_at,
        snapshot=snapshot,
        holidays=holidays,
    )
    return EnrichedTransaction(
        id=uuid7(),
        external_id=item.external_id,
        occurred_at=item.occurred_at,
        received_at=now,
        party_id=relationship.party_id,
        relationship_id=relationship.id,
        account_id=account_id,
        product=relationship.product,
        direction=item.direction,
        rail=item.rail,
        instrument=item.instrument,
        channel=item.channel,
        amount_minor=amount_minor(item.amount),
        currency=item.currency,
        fee_minor=None if item.fee_amount is None else amount_minor(item.fee_amount),
        asset_qty=None if item.crypto is None else item.crypto.asset_qty,
        asset_code=None if item.crypto is None else item.crypto.asset_code,
        unit_amounts=valued.unit_amounts,
        counterparty_id=None,
        counterparty_key=counterparty_key,
        counterparty_country=country,
        counterparty_screening_status=status,
        device_key=_device_key(session, item),
        geo_country=None if item.device is None else item.device.geo_country,
        geo_admin_area=None if item.device is None else item.device.geo_admin_area,
        crypto=(
            None
            if item.crypto is None
            else item.crypto.model_dump(mode="json", exclude={"address"})
        ),
        fx=None if item.fx is None else item.fx.model_dump(mode="json"),
        purpose_code=item.purpose_code,
        period_keys=valued.period_keys,
        tier=relationship.account_tier,
        risk_rating=None,
        monitoring_profile=STANDARD_PROFILE,
        policy_snapshot_id=snapshot.snapshot_id,
        policy_country=snapshot.policy.country,
        late=False,
    )


def apply_enrichment(row: Transaction, txn: EnrichedTransaction) -> None:
    """Store what enrichment computed on the transaction row.

    ``status``, ``late`` and ``processed_at`` are the processing job's to set.
    """
    row.unit_amounts = {
        key: value.model_dump(mode="json") for key, value in txn.unit_amounts.items()
    }
    row.period_keys = txn.period_keys.model_dump(mode="json")
    row.counterparty_country = txn.counterparty_country
    row.counterparty_screening_status = (
        None
        if txn.counterparty_screening_status is None
        else txn.counterparty_screening_status.value
    )
    row.tier = txn.tier
    row.risk_rating = txn.risk_rating
    row.monitoring_profile = txn.monitoring_profile
    row.policy_snapshot_id = txn.policy_snapshot_id


def _row_session(row: Transaction) -> Session:
    session = object_session(row)
    if session is None:
        msg = "enriched_from_row needs a row attached to a session"
        raise ValueError(msg)
    return session


def enriched_from_row(row: Transaction) -> EnrichedTransaction:
    """Rebuild the enriched view of a processed row; nothing is converted again.

    The row stores neither the counterparty's key nor the policy's country, so
    both are read through the row's session's identity map: the counterparty's
    dedup hash by its id and the country from the snapshot, so a replay reads
    each distinct counterparty and snapshot about once.

    Returns:
        The enriched transaction as processing recorded it.

    Raises:
        ValueError: When the row was never enriched or is detached.

    """
    if (
        row.unit_amounts is None
        or row.period_keys is None
        or row.policy_snapshot_id is None
    ):
        msg = "the transaction has not been enriched"
        raise ValueError(msg)
    session = _row_session(row)
    counterparty_key: str | None = None
    if row.counterparty_id is not None:
        dedup_hash, _, _ = status_of(session, row.counterparty_id)
        counterparty_key = dedup_hash.hex()
    snapshot = session.get_one(
        PolicySnapshot,
        (bound_context(session).require_tenant_id(), row.policy_snapshot_id),
    )
    country = str(snapshot.content["country"])
    status = row.counterparty_screening_status
    return EnrichedTransaction(
        id=row.id,
        external_id=row.external_id,
        occurred_at=row.occurred_at,
        received_at=row.received_at,
        party_id=row.party_id,
        relationship_id=row.relationship_id,
        account_id=row.account_id,
        product=row.product,
        direction=row.direction,
        rail=row.rail,
        instrument=row.instrument,
        channel=row.channel,
        amount_minor=row.amount_minor,
        currency=row.currency,
        fee_minor=row.fee_minor,
        asset_qty=row.asset_qty,
        asset_code=row.asset_code,
        unit_amounts={
            key: UnitAmount.model_validate(value)
            for key, value in row.unit_amounts.items()
        },
        counterparty_id=row.counterparty_id,
        counterparty_key=counterparty_key,
        counterparty_country=row.counterparty_country,
        counterparty_screening_status=(
            None if status is None else CounterpartyScreeningStatus(status)
        ),
        device_key=None if row.device_hash is None else row.device_hash.hex(),
        geo_country=row.geo_country,
        geo_admin_area=row.geo_admin_area,
        crypto=_json(row.crypto),
        fx=_json(row.fx),
        purpose_code=row.purpose_code,
        period_keys=PeriodKeys.model_validate(row.period_keys),
        tier=row.tier,
        risk_rating=row.risk_rating,
        monitoring_profile=row.monitoring_profile,
        policy_snapshot_id=row.policy_snapshot_id,
        policy_country=country,
        late=row.late,
    )
