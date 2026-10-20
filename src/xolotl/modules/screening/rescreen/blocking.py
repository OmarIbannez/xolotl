"""Reverse blocking: which of a tenant's parties are near a list entry (spec §10.2).

Forward screening asks the in-memory index which entries are near a party. A list
delta asks the opposite question, and the parties live in Postgres, so the same
blocking plan runs there over ``party_name_index`` (Decision 2: no search service).
A party is a candidate for an entry when one of the entry's names meets the party's
row by the library's ``blocking_plan`` key sets (``phonetic_keys @>`` one set,
served by the GIN index ``ix_pni_phonetic``) or by trigram similarity of the folded
names, or when one of the entry's RFC, CURP or passport numbers is the party's
(the party module's blind-index lookup; nothing is decrypted).

The trigram predicate is ``norm_name % :folded`` after ``SET LOCAL
pg_trgm.similarity_threshold``, not ``similarity(norm_name, :folded) >= 0.4`` as
the spec writes it. Both select the same rows, but only the operator is served by
the trigram GIN index ``ix_pni_trgm``; the function call scans every row.

An entry whose candidates exceed ``BLOCKING_CAP`` parties is not screened in the
delta: it names no one in particular, and screening thousands of parties for one
entry would stall the batch. The caller logs it and the next periodic run covers it.

Everything runs under the caller's tenant session, so row-level security keeps the
search inside the tenant.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, cast, false, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session
from sqlalchemy.types import Text

from xolotl.modules.party.enums import IdentifierType, PartyStatus
from xolotl.modules.party.errors import IdentifierInvalidError
from xolotl.modules.party.service import parties_query
from xolotl.modules.screening.engine.models import PartyNameIndex
from xolotl.modules.screening.lists import IdType, ListEntry
from xolotl.modules.screening.matching import (
    BlockingPlan,
    IdfTable,
    blocking_plan,
    normalize_name,
)

logger = logging.getLogger(__name__)

BLOCKING_CAP = 5000
TRIGRAM_THRESHOLD = 0.4
_ID_TYPES = {
    IdType.RFC: IdentifierType.RFC,
    IdType.CURP: IdentifierType.CURP,
    IdType.PASSPORT: IdentifierType.PASSPORT,
}


@dataclass(frozen=True, slots=True)
class BlockedEntry:
    """The parties one entry was blocked on; ``overflow`` leaves them empty."""

    entry_key: str
    party_ids: frozenset[UUID]
    overflow: bool


def blocking_query(plan: BlockingPlan, folded: str, *, limit: int) -> Select[UUID]:
    """Build the name query of one entry name: any key set, or trigram similarity.

    The trigram operator needs ``pg_trgm.similarity_threshold`` set in the
    transaction, which ``reverse_candidates`` does once.

    Returns:
        The select of matching party ids, at most ``limit`` rows.

    """
    key_sets = [
        PartyNameIndex.phonetic_keys.contains(cast(list(keys), ARRAY(Text)))
        for keys in plan.key_sets
    ]
    trigram = PartyNameIndex.norm_name.op("%")(folded) if folded else false()
    return (
        select(PartyNameIndex.party_id)
        .where(or_(*key_sets, trigram))
        .distinct()
        .limit(limit)
    )


def _name_matches(s: Session, entry: ListEntry, idf: IdfTable) -> set[UUID]:
    found: set[UUID] = set()
    for name in (entry.primary_name, *entry.aliases):
        normalized = normalize_name(
            name.full,
            entity_type=entry.entity_type,
            given=name.given,
            surname=name.surname,
        )
        if not normalized.tokens:
            continue
        query = blocking_query(
            blocking_plan(normalized, idf), normalized.folded, limit=BLOCKING_CAP + 1
        )
        found.update(s.scalars(query))
    return found


def _identifier_matches(s: Session, entry: ListEntry) -> set[UUID]:
    found: set[UUID] = set()
    for identifier in entry.identifiers:
        id_type = _ID_TYPES.get(identifier.id_type)
        if id_type is None:
            continue
        try:
            query = parties_query(
                s,
                status=PartyStatus.ACTIVE,
                id_type=id_type,
                value=identifier.normalized,
            )
        except IdentifierInvalidError:
            # Lists publish malformed RFCs routinely; the names still block.
            logger.debug(
                "screening.blocking_identifier_invalid",
                extra={"list_code": entry.list_code, "entry_key": entry.entry_key},
            )
            continue
        found.update(party.id for party in s.scalars(query))
    return found


def reverse_candidates(
    s: Session, entries: Sequence[ListEntry], *, idf: IdfTable
) -> list[BlockedEntry]:
    """Find the tenant's parties near each entry, by name and by identifier.

    Returns:
        One ``BlockedEntry`` per entry, in the order given. An entry with more than
        ``BLOCKING_CAP`` candidates is marked ``overflow`` with no party ids.

    """
    _ = s.execute(
        text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"),
        {"t": str(TRIGRAM_THRESHOLD)},
    )
    blocked: list[BlockedEntry] = []
    for entry in entries:
        party_ids = _name_matches(s, entry, idf) | _identifier_matches(s, entry)
        if len(party_ids) > BLOCKING_CAP:
            blocked.append(BlockedEntry(entry.entry_key, frozenset(), overflow=True))
        else:
            blocked.append(
                BlockedEntry(entry.entry_key, frozenset(party_ids), overflow=False)
            )
    return blocked
