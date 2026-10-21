"""The masked and the revealed view of a counterparty (SP-04 plan 4a, A10).

``counterparty_summary`` never decrypts: its ``has_*`` flags come from the blind
index columns. ``reveal_counterparty`` records one ``VIEW_PII`` row per value it is
about to show, all of them before the first decryption (the party module's
precedent), so a failing key read rolls the rows back with the request and a value
is never shown unaudited. Rows are read through the session's tenant: another
tenant's counterparty is not found. Monitoring's transaction views and plan 4c's
counterparty pages (C3) call both.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.schemas import (
    CounterpartySummary,
    RevealedCounterparty,
)
from xolotl.platform.audit import VIEW_PII, AuditEvent, record
from xolotl.platform.crypto.encrypted import Encrypted
from xolotl.platform.db import bound_context
from xolotl.platform.errors import NotFoundError

ENTITY = "counterparty"


def _row(session: Session, counterparty_id: UUID) -> Counterparty:
    tenant_id = bound_context(session).require_tenant_id()
    row = session.get(Counterparty, (tenant_id, counterparty_id))
    if row is None:
        msg = "counterparty not found"
        raise NotFoundError(msg)
    return row


def _summary(row: Counterparty) -> CounterpartySummary:
    return CounterpartySummary(
        id=row.id,
        kind=row.kind,
        has_name=row.name_hash is not None,
        has_account=row.account_hash is not None,
        has_wallet=row.wallet_hash is not None,
        has_identifier=row.identifier_hash is not None,
        institution=row.institution,
        country=row.country,
        wallet_network=row.wallet_network,
        screening_status=row.screening_status,
        txn_count=row.txn_count,
    )


def counterparty_summary(
    session: Session, counterparty_id: UUID
) -> CounterpartySummary:
    """Describe a counterparty for staff without decrypting anything.

    Another tenant's counterparty, or an unknown id, is ``NotFoundError``.

    Returns:
        The masked summary.

    """
    return _summary(_row(session, counterparty_id))


def counterparty_summaries(
    session: Session, counterparty_ids: Iterable[UUID]
) -> dict[UUID, CounterpartySummary]:
    """Describe several counterparties at once, for a page of transactions.

    Returns:
        The masked summary of each id the session's tenant has, by id.

    """
    wanted = set(counterparty_ids)
    if not wanted:
        return {}
    rows = session.scalars(select(Counterparty).where(Counterparty.id.in_(wanted)))
    return {row.id: _summary(row) for row in rows}


def _shown(
    session: Session, row: Counterparty, field: str, value: Encrypted[str] | None
) -> Encrypted[str] | None:
    # Audited here, decrypted only once every field has been audited.
    if value is None:
        return None
    _ = record(
        session,
        AuditEvent(
            action=VIEW_PII,
            entity_type=ENTITY,
            entity_id=str(row.id),
            detail={"field": field},
        ),
    )
    return value


def _plain(session: Session, value: Encrypted[str] | None) -> str | None:
    return None if value is None else value.reveal(session=session)


def reveal_counterparty(
    session: Session, counterparty_id: UUID
) -> RevealedCounterparty:
    """Show a counterparty's values to a person, auditing each one first.

    One ``VIEW_PII`` row (entity ``counterparty``, detail ``{"field": <column>}``)
    per non-null value, in the order name, account, wallet, identifier, all of them
    recorded before the first decryption. Another tenant's counterparty, or an
    unknown id, is ``NotFoundError``.

    Returns:
        The plaintext values.

    """
    row = _row(session, counterparty_id)
    name = _shown(session, row, "name", row.name)
    account = _shown(session, row, "account", row.account)
    wallet = _shown(session, row, "wallet", row.wallet)
    identifier = _shown(session, row, "identifier", row.identifier)
    return RevealedCounterparty(
        name=_plain(session, name),
        account=_plain(session, account),
        wallet_address=_plain(session, wallet),
        wallet_network=row.wallet_network,
        identifier_type=(
            None if row.identifier_type is None else row.identifier_type.value
        ),
        identifier=_plain(session, identifier),
    )
