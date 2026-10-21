"""Counterparty resolution through blind indexes (SP-04 spec §11.1, Decision 18).

A counterparty is one row per tenant and ``dedup_hash``. The hash is an HMAC under
the tenant's INDEX key over four component blind indexes (plan 4a, Global
Constraints "Keys and hashes"):

- the name, folded by the screening library's ``normalize_name`` and indexed as
  ``name``;
- the account, stripped of surrounding whitespace and indexed as ``account``
  (plan 4a Deviation 17: ``normalize_identifier`` knows no account type yet);
- the wallet address as sent, case kept, so it is an HMAC of its own rather than a
  ``blind_index``, which would fold it;
- the identifier, indexed under its type with the party module's rules.

An absent component, or one with nothing left to index, contributes ``b""``.
``resolve`` upserts the row in one statement; ``dedup_hash_of`` and
``status_by_dedup_hash`` read without writing, for the decide path.
"""

import hmac
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Boolean, literal_column, select, text
from sqlalchemy.dialects.postgresql import insert
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
from xolotl.platform.crypto import Encrypted, encrypt
from xolotl.platform.crypto.blind_index import blind_index, normalize_identifier
from xolotl.platform.crypto.provider import get_key_provider
from xolotl.platform.db import bound_context
from xolotl.platform.ids import uuid7

_ENTITY_BY_KIND: dict[CounterpartyKind, EntityType] = {
    CounterpartyKind.PERSON: EntityType.PERSON,
    CounterpartyKind.ORGANIZATION: EntityType.ORGANIZATION,
    CounterpartyKind.UNKNOWN: EntityType.OTHER,
}


class CounterpartyUnindexableError(ValueError):
    """No component of a counterparty input leaves anything to index."""


@dataclass(frozen=True, slots=True)
class ComponentHashes:
    name_hash: bytes | None
    account_hash: bytes | None
    wallet_hash: bytes | None
    identifier_hash: bytes | None

    def any(self) -> bool:
        """Tell whether at least one component was indexed.

        Returns:
            True when some hash is present.

        """
        return any((
            self.name_hash,
            self.account_hash,
            self.wallet_hash,
            self.identifier_hash,
        ))


@dataclass(frozen=True, slots=True)
class CounterpartyRef:
    id: UUID
    dedup_hash: bytes
    country: str | None
    screening_status: CounterpartyScreeningStatus
    created: bool


def _tenant(session: Session) -> UUID:
    return bound_context(session).require_tenant_id()


def _folded(name: str, entity_type: EntityType) -> str:
    return normalize_name(name, entity_type=entity_type).folded


def name_hash_of(session: Session, name: str, *, entity_type: EntityType) -> bytes:
    """Blind-index a counterparty name in its library-folded form.

    Returns:
        The 32-byte tag.

    """
    return blind_index(
        "name",
        _folded(name, entity_type),
        tenant_id=_tenant(session),
        session=session,
    )


def account_hash_of(session: Session, account: str) -> bytes:
    """Blind-index a counterparty account as sent, stripped (Deviation 17).

    Returns:
        The 32-byte tag.

    """
    return blind_index(
        "account", account.strip(), tenant_id=_tenant(session), session=session
    )


def wallet_hash_of(session: Session, address: str) -> bytes:
    """HMAC a wallet address with its case kept, under the tenant's INDEX key.

    Returns:
        The 32-byte tag.

    """
    key = get_key_provider().index_key(_tenant(session), session=session)
    return hmac.digest(key, b"wallet:" + address.encode(), "sha256")


def identifier_hash_of(session: Session, id_type: IdentifierType, value: str) -> bytes:
    """Blind-index an identifier under its type, as the party module indexes one.

    Returns:
        The 32-byte tag.

    """
    return blind_index(
        id_type.value.lower(), value, tenant_id=_tenant(session), session=session
    )


def component_hashes(
    session: Session, counterparty: CounterpartyInput
) -> ComponentHashes:
    """Compute the four component tags of an input; None where there is none.

    A component with nothing left after normalisation (a name of punctuation
    only) is treated as absent: every such value would share one tag.

    Returns:
        The component hashes.

    """
    entity_type = _ENTITY_BY_KIND[counterparty.kind]
    name_hash = None
    if counterparty.name and normalize_identifier(
        "name", _folded(counterparty.name, entity_type)
    ):
        name_hash = name_hash_of(session, counterparty.name, entity_type=entity_type)
    account_hash = None
    if counterparty.account and normalize_identifier(
        "account", counterparty.account.strip()
    ):
        account_hash = account_hash_of(session, counterparty.account)
    wallet_hash = None
    if counterparty.wallet_address:
        wallet_hash = wallet_hash_of(session, counterparty.wallet_address)
    identifier_hash = None
    identifier = counterparty.identifier
    if identifier is not None and normalize_identifier(
        identifier.id_type.value, identifier.value
    ):
        identifier_hash = identifier_hash_of(
            session, identifier.id_type, identifier.value
        )
    return ComponentHashes(
        name_hash=name_hash,
        account_hash=account_hash,
        wallet_hash=wallet_hash,
        identifier_hash=identifier_hash,
    )


def _dedup(session: Session, hashes: ComponentHashes) -> bytes:
    if not hashes.any():
        msg = "the counterparty has nothing to index"
        raise CounterpartyUnindexableError(msg)
    key = get_key_provider().index_key(_tenant(session), session=session)
    message = b"|".join(
        part or b""
        for part in (
            hashes.name_hash,
            hashes.account_hash,
            hashes.wallet_hash,
            hashes.identifier_hash,
        )
    )
    return hmac.digest(key, b"counterparty:" + message, "sha256")


def dedup_hash_of(session: Session, counterparty: CounterpartyInput) -> bytes:
    """Compute the key one counterparty is kept under, without writing anything.

    An input with nothing to index raises ``CounterpartyUnindexableError``.

    Returns:
        The 32-byte HMAC over the four component hashes.

    """
    return _dedup(session, component_hashes(session, counterparty))


def resolve(session: Session, counterparty: CounterpartyInput) -> CounterpartyRef:
    """Upsert the counterparty an input names and count one more transaction.

    A new row stores the name, account, wallet and identifier as ciphertext under
    the tenant's DATA key beside their tags; a known one gains ``last_seen_at``
    and one ``txn_count`` and keeps what it first stored. An input with nothing
    to index raises ``CounterpartyUnindexableError``.

    Returns:
        The row's id, key, country and cached screening status, and whether this
        call created it.

    """
    tenant_id = _tenant(session)
    hashes = component_hashes(session, counterparty)
    dedup_hash = _dedup(session, hashes)

    def seal(value: str | None) -> Encrypted[str] | None:
        return (
            None
            if value is None
            else encrypt(value, tenant_id=tenant_id, session=session)
        )

    identifier = counterparty.identifier
    table = Counterparty.__table__
    statement = (
        insert(Counterparty)
        .values(
            tenant_id=tenant_id,
            id=uuid7(),
            kind=counterparty.kind,
            name=seal(counterparty.name),
            name_hash=hashes.name_hash,
            account=seal(counterparty.account),
            account_hash=hashes.account_hash,
            institution=counterparty.institution,
            country=counterparty.country,
            wallet=seal(counterparty.wallet_address),
            wallet_hash=hashes.wallet_hash,
            wallet_network=counterparty.wallet_network,
            identifier_type=None if identifier is None else identifier.id_type,
            identifier=seal(None if identifier is None else identifier.value),
            identifier_hash=hashes.identifier_hash,
            dedup_hash=dedup_hash,
            txn_count=1,
        )
        .on_conflict_do_update(
            index_elements=[Counterparty.tenant_id, Counterparty.dedup_hash],
            set_={
                "last_seen_at": text("now()"),
                "txn_count": table.c.txn_count + 1,
            },
        )
        .returning(
            Counterparty.id,
            Counterparty.country,
            Counterparty.screening_status,
            literal_column("xmax = 0", Boolean).label("created"),
        )
    )
    row = session.execute(statement).one()
    return CounterpartyRef(
        id=row[0],
        dedup_hash=dedup_hash,
        country=row[1],
        screening_status=row[2],
        created=row[3],
    )


def status_by_dedup_hash(
    session: Session, dedup_hash: bytes
) -> CounterpartyScreeningStatus:
    """Read a counterparty's cached screening status by its key (DR-308).

    Returns:
        The stored status, or ``UNSCREENED`` for a counterparty never seen.

    """
    status = session.scalar(
        select(Counterparty.screening_status).where(
            Counterparty.dedup_hash == dedup_hash
        )
    )
    return CounterpartyScreeningStatus.UNSCREENED if status is None else status
