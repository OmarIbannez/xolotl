"""Reverse blocking of counterparties near a list entry (SP-04 spec §11.3).

The list-delta re-screen asks which of a tenant's counterparties are near each
added or changed entry, as it asks of parties (SP-03 spec §10.2). By name, the
party blocking queries run over ``counterparty_name_index`` (``blocking_query`` and
``stop_keys`` with ``table=CounterpartyNameIndex``), so the key sets, the trigram
predicate and XOL-107's stop-key rule are the same, the rule counting the
counterparties' own rows.

By identifier, nothing is decrypted: each of the entry's RFC, CURP and passport
numbers that passes the party module's validator is blind-indexed as resolve
indexes a counterparty's identifier (``identifier_hash_of``) and looked up in
``counterparty.identifier_hash``; each digital-currency address is HMACed as
resolve HMACs a wallet (``wallet_hash_of``, whitespace removed and case kept on
both sides) and looked up in ``counterparty.wallet_hash``, so a wallet-only
counterparty is found without a name and only by the whole, exact address.

An entry with more than ``BLOCKING_CAP`` counterparties overflows as a party entry
does: its name candidates are dropped and its identifier and wallet matches kept.
Everything runs under the caller's tenant session.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, and_, or_, select, text
from sqlalchemy.orm import Session

from xolotl.modules.party.identifiers import IdentifierError, validate
from xolotl.modules.screening.counterparty.models import (
    Counterparty,
    CounterpartyNameIndex,
)
from xolotl.modules.screening.counterparty.resolve import (
    identifier_hash_of,
    wallet_hash_of,
)
from xolotl.modules.screening.engine.queries import SCREENED_ID_TYPES
from xolotl.modules.screening.lists import IdType, ListEntry
from xolotl.modules.screening.matching import IdfTable
from xolotl.modules.screening.rescreen.blocking import (
    BLOCKING_CAP,
    TRIGRAM_THRESHOLD,
    blocking_query,
    name_plans,
    stop_keys,
)

logger = logging.getLogger(__name__)

_ID_TYPES = {id_type: party_type for party_type, id_type in SCREENED_ID_TYPES.items()}


@dataclass(frozen=True, slots=True)
class BlockedCounterparty:
    """The counterparties one entry was blocked on.

    On ``overflow`` the name candidates are dropped and ``counterparty_ids`` holds
    only the entry's identifier and wallet matches.
    """

    entry_key: str
    counterparty_ids: frozenset[UUID]
    overflow: bool


def _identifier_terms(s: Session, entry: ListEntry) -> list[ColumnElement[bool]]:
    terms: list[ColumnElement[bool]] = []
    for identifier in entry.identifiers:
        if identifier.id_type is IdType.DIGITAL_CURRENCY_ADDRESS:
            if identifier.normalized:
                tag = wallet_hash_of(s, identifier.normalized)
                terms.append(Counterparty.wallet_hash == tag)
            continue
        id_type = _ID_TYPES.get(identifier.id_type)
        if id_type is None:
            continue
        result = validate(id_type, identifier.normalized)
        if isinstance(result, IdentifierError):
            # Lists publish malformed RFCs routinely; the names still block.
            logger.debug(
                "screening.blocking_identifier_invalid",
                extra={"list_code": entry.list_code, "entry_key": entry.entry_key},
            )
            continue
        tag = identifier_hash_of(s, id_type, result.value)
        terms.append(
            and_(
                Counterparty.identifier_type == id_type,
                Counterparty.identifier_hash == tag,
            )
        )
    return terms


def _identifier_matches(s: Session, entry: ListEntry) -> set[UUID]:
    terms = _identifier_terms(s, entry)
    if not terms:
        return set()
    return set(s.scalars(select(Counterparty.id).where(or_(*terms))))


def reverse_counterparty_candidates(
    s: Session, entries: Sequence[ListEntry], *, idf: IdfTable
) -> list[BlockedCounterparty]:
    """Find the tenant's counterparties near each entry, by name, identifier, wallet.

    Returns:
        One ``BlockedCounterparty`` per entry, in the order given. An entry with
        more than ``BLOCKING_CAP`` candidates is marked ``overflow`` and keeps only
        its identifier and wallet matches.

    """
    _ = s.execute(
        text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"),
        {"t": str(TRIGRAM_THRESHOLD)},
    )
    plans = [name_plans(entry, idf) for entry in entries]
    stop = stop_keys(
        s,
        (plan for entry_plans in plans for plan, _ in entry_plans),
        table=CounterpartyNameIndex,
    )
    blocked: list[BlockedCounterparty] = []
    for entry, entry_plans in zip(entries, plans, strict=True):
        by_id = _identifier_matches(s, entry)
        found = set(by_id)
        for plan, folded in entry_plans:
            query = blocking_query(
                plan,
                folded,
                limit=BLOCKING_CAP + 1,
                stop=stop,
                table=CounterpartyNameIndex,
            )
            found.update(s.scalars(query))
        overflow = len(found) > BLOCKING_CAP
        kept = by_id if overflow else found
        blocked.append(
            BlockedCounterparty(entry.entry_key, frozenset(kept), overflow=overflow)
        )
    return blocked
