"""Counterparty keys and the status reads (SP-04 spec §11.1, plan 4a A4 and A6).

Every hash is an HMAC under the tenant's INDEX key (plan 4a Global Constraints,
"Keys and hashes"): the name through ``blind_index("name", …)`` over the SP-03
library's folded form, the account as sent without whitespace (Deviation 17), the
identifier under its type, and the wallet address with its case kept. The dedup
hash joins the four with ``b""`` for an absent component, so one counterparty has
one row per tenant whichever transaction names it. ``status_by_dedup_hash`` is the
decide path's read (no row is written); ``status_of`` is the processing path's,
by the id the ingest stored on the transaction.
"""

import hmac
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.party.enums import IdentifierType
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyInput,
    CounterpartyKind,
    CounterpartyScreeningStatus,
)
from xolotl.modules.screening.lists.types import EntityType
from xolotl.modules.screening.matching.normalize import normalize_name
from xolotl.platform.crypto import get_key_provider
from xolotl.platform.crypto.blind_index import blind_index
from xolotl.platform.db import bound_context

_ENTITY_OF: dict[CounterpartyKind, EntityType] = {
    CounterpartyKind.PERSON: EntityType.PERSON,
    CounterpartyKind.ORGANIZATION: EntityType.ORGANIZATION,
    CounterpartyKind.UNKNOWN: EntityType.OTHER,
}


def _tenant(session: Session) -> UUID:
    return bound_context(session).require_tenant_id()


def _hmac(session: Session, message: bytes) -> bytes:
    key = get_key_provider().index_key(_tenant(session), session=session)
    return hmac.digest(key, message, "sha256")


def name_hash_of(session: Session, name: str, *, entity_type: EntityType) -> bytes:
    """Blind-index a counterparty's name in the library's folded form.

    Returns:
        The 32-byte tag.

    """
    folded = normalize_name(name, entity_type=entity_type).folded
    return blind_index("name", folded, tenant_id=_tenant(session), session=session)


def account_hash_of(session: Session, account: str) -> bytes:
    """Blind-index an account as sent, stripped of whitespace (Deviation 17).

    Returns:
        The 32-byte tag.

    """
    return blind_index(
        "account", account.strip(), tenant_id=_tenant(session), session=session
    )


def wallet_hash_of(session: Session, address: str) -> bytes:
    """Key a wallet address with its case kept (``blind_index`` would fold it).

    Returns:
        The 32-byte tag.

    """
    return _hmac(session, b"wallet:" + address.encode())


def identifier_hash_of(session: Session, id_type: IdentifierType, value: str) -> bytes:
    """Blind-index an identifier under its type, as the party module does.

    Returns:
        The 32-byte tag.

    """
    return blind_index(
        id_type.value.lower(), value, tenant_id=_tenant(session), session=session
    )


def dedup_hash_of(session: Session, input: CounterpartyInput) -> bytes:  # ruff: ignore[builtin-argument-shadowing]
    """Compute the key one counterparty has in its tenant; nothing is written.

    Returns:
        The HMAC over the four component hashes, ``b""`` for each absent one.

    """
    name = (
        name_hash_of(session, input.name, entity_type=_ENTITY_OF[input.kind])
        if input.name
        else b""
    )
    account = account_hash_of(session, input.account) if input.account else b""
    wallet = (
        wallet_hash_of(session, input.wallet_address) if input.wallet_address else b""
    )
    identifier = (
        identifier_hash_of(session, input.identifier.id_type, input.identifier.value)
        if input.identifier is not None
        else b""
    )
    return _hmac(
        session,
        b"counterparty:" + b"|".join((name, account, wallet, identifier)),
    )


def status_by_dedup_hash(
    session: Session, dedup_hash: bytes
) -> CounterpartyScreeningStatus:
    """Read a counterparty's screening status by its key (the decide path).

    Returns:
        The stored status, or ``UNSCREENED`` for a counterparty never seen.

    """
    found = session.scalar(
        select(Counterparty.screening_status).where(
            Counterparty.dedup_hash == dedup_hash
        )
    )
    return CounterpartyScreeningStatus.UNSCREENED if found is None else found


def status_of(
    session: Session, counterparty_id: UUID
) -> tuple[bytes, str | None, CounterpartyScreeningStatus]:
    """Read what enrichment records of a stored counterparty, at this moment.

    Returns:
        Its dedup hash, country and screening status.

    """
    dedup_hash, country, status = session.execute(
        select(
            Counterparty.dedup_hash,
            Counterparty.country,
            Counterparty.screening_status,
        ).where(Counterparty.id == counterparty_id)
    ).one()
    return dedup_hash, country, status
