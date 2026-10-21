"""The client, staff and revealed views of a transaction (SP-04 spec §15, A10).

``client_view`` is all an API client ever gets (D20, tipping-off). ``staff_views``
builds a page of staff views with one query each for the accounts, the parked rows
and the counterparties; the counterparty is masked (``counterparty_summary``) and the
parked row is joined only while the transaction's status is ``PARKED``, since the
sweep keeps a row's ``transaction_parked`` entry when it un-parks it. A12's parked
tab reads that. ``revealed_view`` records every ``VIEW_PII`` row (``raw`` and the
counterparty on the transaction, then each counterparty value through
``reveal_counterparty``) before it decrypts anything.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.schemas import (
    ParkedInfo,
    TransactionClientView,
    TransactionRevealedView,
    TransactionStaffView,
    UnitAmountView,
)
from xolotl.modules.monitoring.txn.models import Account, Transaction, TransactionParked
from xolotl.modules.monitoring.txn.schema import PeriodKeys, UnitAmount
from xolotl.modules.screening.counterparty.schema import CounterpartyScreeningStatus
from xolotl.modules.screening.counterparty.schemas import CounterpartySummary
from xolotl.modules.screening.counterparty.views import (
    counterparty_summaries,
    reveal_counterparty,
)
from xolotl.platform.audit import VIEW_PII, AuditEvent, record
from xolotl.platform.units.rules import ConversionRule

if TYPE_CHECKING:
    from uuid import UUID

ENTITY = "transaction"
_JSON_OBJECT: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])


def client_view(row: Transaction) -> TransactionClientView:
    """Show an API client a transaction: identity and status, nothing else.

    Returns:
        The client view.

    """
    return TransactionClientView(
        id=row.id,
        external_id=row.external_id,
        occurred_at=row.occurred_at,
        status=row.status,
        late=row.late,
    )


def _unit_amounts(row: Transaction) -> list[UnitAmountView]:
    if row.unit_amounts is None:
        return []
    views: list[UnitAmountView] = []
    for key in sorted(row.unit_amounts):
        unit, _, rule = key.partition("/")
        value = UnitAmount.model_validate(row.unit_amounts[key])
        views.append(
            UnitAmountView(
                key=key,
                unit=unit,
                rule=ConversionRule(rule),
                amount=value.amount,
                unit_value_id=value.unit_value_id,
                resolved_date=value.resolved_date,
            )
        )
    return views


def _json(value: dict[str, object] | None) -> dict[str, JsonValue] | None:
    return None if value is None else _JSON_OBJECT.validate_python(value)


def _parked(row: TransactionParked | None) -> ParkedInfo | None:
    if row is None:
        return None
    return ParkedInfo(
        reason=row.reason,
        unit_code=row.unit_code,
        needed_on=row.needed_on,
        attempts=row.attempts,
        parked_at=row.parked_at,
        last_error=row.last_error,
    )


def _staff(
    row: Transaction,
    *,
    account_ref: str,
    parked: TransactionParked | None,
    counterparty: CounterpartySummary | None,
) -> TransactionStaffView:
    status = row.counterparty_screening_status
    return TransactionStaffView(
        id=row.id,
        external_id=row.external_id,
        occurred_at=row.occurred_at,
        status=row.status,
        late=row.late,
        received_at=row.received_at,
        batch_id=row.batch_id,
        party_id=row.party_id,
        relationship_id=row.relationship_id,
        account_id=row.account_id,
        account_ref=account_ref,
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
        unit_amounts=_unit_amounts(row),
        period_keys=(
            None
            if row.period_keys is None
            else PeriodKeys.model_validate(row.period_keys)
        ),
        counterparty=counterparty,
        counterparty_screening_status_at_processing=(
            None if status is None else CounterpartyScreeningStatus(status)
        ),
        device_present=row.device_hash is not None,
        geo_country=row.geo_country,
        geo_admin_area=row.geo_admin_area,
        crypto=_json(row.crypto),
        fx=_json(row.fx),
        purpose_code=row.purpose_code,
        tier=row.tier,
        risk_rating=row.risk_rating,
        monitoring_profile=row.monitoring_profile,
        policy_snapshot_id=row.policy_snapshot_id,
        processed_at=row.processed_at,
        decision_id=row.decision_id,
        parked=_parked(parked),
    )


def staff_views(
    session: Session, rows: Sequence[Transaction]
) -> list[TransactionStaffView]:
    """Show staff a page of transactions, each counterparty masked.

    Returns:
        One staff view per row, in the given order.

    """
    if not rows:
        return []
    account_ids = {row.account_id for row in rows}
    accounts: dict[UUID, str] = dict(
        session.execute(
            select(Account.id, Account.external_ref).where(Account.id.in_(account_ids))
        ).all()
    )
    parked_keys = [
        (row.occurred_at, row.id)
        for row in rows
        if row.status is TransactionStatus.PARKED
    ]
    parked: dict[UUID, TransactionParked] = {}
    if parked_keys:
        parked = {
            entry.transaction_id: entry
            for entry in session.scalars(
                select(TransactionParked).where(
                    tuple_(
                        TransactionParked.occurred_at, TransactionParked.transaction_id
                    ).in_(parked_keys)
                )
            )
        }
    counterparties = counterparty_summaries(
        session, {row.counterparty_id for row in rows if row.counterparty_id}
    )
    return [
        _staff(
            row,
            account_ref=accounts[row.account_id],
            parked=parked.get(row.id),
            counterparty=(
                None
                if row.counterparty_id is None
                else counterparties[row.counterparty_id]
            ),
        )
        for row in rows
    ]


def staff_view(session: Session, row: Transaction) -> TransactionStaffView:
    """Show staff one transaction, its counterparty masked.

    Returns:
        The staff view.

    """
    return staff_views(session, [row])[0]


def _audit(session: Session, row: Transaction, field: str) -> None:
    _ = record(
        session,
        AuditEvent(
            action=VIEW_PII,
            entity_type=ENTITY,
            entity_id=str(row.id),
            detail={"field": field},
        ),
    )


def revealed_view(session: Session, row: Transaction) -> TransactionRevealedView:
    """Show staff a transaction with its counterparty and payload decrypted.

    ``VIEW_PII`` is recorded on the transaction for ``raw`` and, when it has one,
    for ``counterparty``, then once per counterparty value by
    ``reveal_counterparty``; the payload is decrypted last, after every row.

    Returns:
        The revealed view.

    """
    staff = staff_view(session, row)
    _audit(session, row, "raw")
    revealed = None
    if row.counterparty_id is not None:
        _audit(session, row, "counterparty")
        revealed = reveal_counterparty(session, row.counterparty_id)
    raw = _JSON_OBJECT.validate_python(row.raw.reveal(session=session))
    return TransactionRevealedView.model_validate({
        **staff.model_dump(),
        "counterparty_revealed": revealed,
        "raw": raw,
    })
