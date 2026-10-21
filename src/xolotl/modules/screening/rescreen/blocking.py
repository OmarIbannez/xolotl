"""Reverse blocking: which of a tenant's parties are near a list entry (spec §10.2).

Forward screening asks the in-memory index which entries are near a party. A list
delta asks the opposite question, and the parties live in Postgres, so the same
blocking plan runs there over ``party_name_index`` (Decision 2: no search service).
A party is a candidate for an entry when one of the entry's names meets the party's
row by the library's ``blocking_plan`` key sets (``phonetic_keys @>`` one set,
against the library's ``name_keys`` the row stores) or by trigram similarity of the
folded names, or when one of the entry's RFC, CURP or passport numbers is the party's
(the party module's blind-index lookup; nothing is decrypted).

The trigram predicate is ``norm_name % :folded`` after ``SET LOCAL
pg_trgm.similarity_threshold``, not ``similarity(norm_name, :folded) >= 0.4`` as
the spec writes it. Both select the same rows, and only the operator could be
served by the trigram GIN index ``ix_pni_trgm``.

Neither GIN index is used today: ``party_name_index`` has forced row-level security,
and ``@>`` and ``%`` are not LEAKPROOF, so Postgres applies the tenant policy first
(through the primary key) and filters the tenant's rows with both predicates. Each
name query therefore scans the tenant's rows, which the product owner accepted for
the MVP. A SECURITY DEFINER blocking function or LEAKPROOF operators would let the
indexes serve the query if tenant volume grows.

Stop keys mirror the forward index's rule (``STOP_KEY_POSTINGS``) on the party
side. The library files a rare token under its phonetic key, that key's one-letter
deletions and its ``p:``/``s:`` affix keys, each a single-key set; on a Mexican base
keys like ``rs``, ``p:mar`` or ``s:rez`` are held by a tenth of the parties, so a
page of a hundred entries would block nearly the whole base. Once per page, one
aggregate query counts the holders of the page's single keys in the tenant's
``party_name_index``, and a single-key set whose key is held by more than
``STOP_KEY_SHARE`` of the tenant's name rows, and by more than
``STOP_KEY_MIN_HOLDERS`` of them, is skipped silently (a debug log at most). Pair
sets, the trigram predicate and identifier matches are untouched, so the party is
still found through the rest of its name. Measured on the perf test's 10,000-party
base (XOL-107): thresholds from 100 to 300 holders cut the delta's blocked union
from 9,970 parties to the 500 near ones and keep the corpus's retrieval recall at
0.996 to 0.998; 500 lets the union back to 9,200 and 50 breaks the 0.995 floor. The
share scales with the base, the floor keeps small tenants' cheap keys.

An entry whose candidates exceed ``BLOCKING_CAP`` parties overflows: its name
candidates are not screened in the delta, since a name that common names no one in
particular and screening thousands of parties for one entry would stall the batch.
The caller logs it and the next periodic run covers those names. Its identifier
matches are kept: an exact RFC, CURP or passport match is never deferred.

Counterparties (SP-04 spec §11.3) are blocked by the same queries over
``counterparty_name_index``: ``blocking_query`` and ``stop_keys`` take the index
model as ``table`` (``PartyNameIndex`` by default), and the stop-key rule counts the
counterparties' own rows. ``screening.counterparty.blocking`` adds the lookups by
wallet address and identifier hash.

Everything runs under the caller's tenant session, so row-level security keeps the
search inside the tenant.
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, any_, cast, false, func, or_, select, text, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session
from sqlalchemy.types import Text

from xolotl.modules.party.enums import IdentifierType, PartyStatus
from xolotl.modules.party.errors import IdentifierInvalidError
from xolotl.modules.party.service import parties_query
from xolotl.modules.screening.counterparty.models import CounterpartyNameIndex
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
STOP_KEY_SHARE = 0.02
STOP_KEY_MIN_HOLDERS = 100
NO_STOP_KEYS: frozenset[str] = frozenset()
# The name index a blocking query reads: the parties', or the counterparties'
# (SP-04 spec §11.3), which share the columns blocking reads.
type NameIndexTable = type[PartyNameIndex | CounterpartyNameIndex]
_ID_TYPES = {
    IdType.RFC: IdentifierType.RFC,
    IdType.CURP: IdentifierType.CURP,
    IdType.PASSPORT: IdentifierType.PASSPORT,
}


@dataclass(frozen=True, slots=True)
class BlockedEntry:
    """The parties one entry was blocked on.

    On ``overflow`` the name candidates are dropped and ``party_ids`` holds only the
    entry's identifier matches.
    """

    entry_key: str
    party_ids: frozenset[UUID]
    overflow: bool


def name_plans(entry: ListEntry, idf: IdfTable) -> list[tuple[BlockingPlan, str]]:
    """Plan each of the entry's names: its blocking plan and its folded form.

    Returns:
        One ``(plan, folded)`` per name with tokens, the primary name first.

    """
    plans: list[tuple[BlockingPlan, str]] = []
    for name in (entry.primary_name, *entry.aliases):
        normalized = normalize_name(
            name.full,
            entity_type=entry.entity_type,
            given=name.given,
            surname=name.surname,
        )
        if normalized.tokens:
            plans.append((blocking_plan(normalized, idf), normalized.folded))
    return plans


def stop_keys(
    s: Session, plans: Iterable[BlockingPlan], *, table: NameIndexTable = PartyNameIndex
) -> frozenset[str]:
    """Find the plans' single keys too common in the tenant's base to block on.

    One aggregate query under the tenant's session counts the rows of ``table``
    holding each key; row-level security keeps the counts to the tenant's own rows.
    The counterparties' index is counted over its own rows, never the parties'.

    Returns:
        The keys held by more than ``STOP_KEY_SHARE`` of the tenant's name rows and
        by more than ``STOP_KEY_MIN_HOLDERS`` of them.

    """
    singles = {keys[0] for plan in plans for keys in plan.key_sets if len(keys) == 1}
    if not singles:
        return frozenset()
    rows = s.scalar(select(func.count()).select_from(table)) or 0
    threshold = max(STOP_KEY_MIN_HOLDERS, STOP_KEY_SHARE * rows)
    if rows <= threshold:
        return frozenset()
    held = func.unnest(table.phonetic_keys).table_valued("key")
    held = held.render_derived()
    key = held.c.key
    query = (
        select(key)
        .select_from(table)
        .join(held, true())
        .where(key == any_(cast(sorted(singles), ARRAY(Text))))
        .group_by(key)
        .having(func.count() > threshold)
    )
    dropped = frozenset(s.scalars(query))
    if dropped:
        logger.debug(
            "screening.blocking_stop_keys",
            extra={"dropped": len(dropped), "rows": rows, "threshold": threshold},
        )
    return dropped


def blocking_query(
    plan: BlockingPlan,
    folded: str,
    *,
    limit: int,
    stop: frozenset[str] = NO_STOP_KEYS,
    table: NameIndexTable = PartyNameIndex,
) -> Select[UUID]:
    """Build the name query of one entry name: any key set, or trigram similarity.

    Single-key sets whose key is in ``stop`` are left out. The trigram operator
    needs ``pg_trgm.similarity_threshold`` set in the transaction, which
    ``reverse_candidates`` does once.

    Returns:
        The select of matching party ids, or counterparty ids when ``table`` is
        ``CounterpartyNameIndex``, at most ``limit`` rows.

    """
    key_sets = [
        table.phonetic_keys.contains(cast(list(keys), ARRAY(Text)))
        for keys in plan.key_sets
        if len(keys) != 1 or keys[0] not in stop
    ]
    trigram = table.norm_name.op("%")(folded) if folded else false()
    subject = (
        table.party_id if issubclass(table, PartyNameIndex) else table.counterparty_id
    )
    return select(subject).where(or_(*key_sets, trigram)).distinct().limit(limit)


def _name_matches(
    s: Session, plans: list[tuple[BlockingPlan, str]], stop: frozenset[str]
) -> set[UUID]:
    found: set[UUID] = set()
    for plan, folded in plans:
        query = blocking_query(plan, folded, limit=BLOCKING_CAP + 1, stop=stop)
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
        ``BLOCKING_CAP`` candidates is marked ``overflow`` and keeps only its
        identifier matches.

    """
    _ = s.execute(
        text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"),
        {"t": str(TRIGRAM_THRESHOLD)},
    )
    plans = [name_plans(entry, idf) for entry in entries]
    stop = stop_keys(s, (plan for entry_plans in plans for plan, _ in entry_plans))
    blocked: list[BlockedEntry] = []
    for entry, entry_plans in zip(entries, plans, strict=True):
        by_id = _identifier_matches(s, entry)
        party_ids = _name_matches(s, entry_plans, stop) | by_id
        overflow = len(party_ids) > BLOCKING_CAP
        kept = by_id if overflow else party_ids
        blocked.append(
            BlockedEntry(entry.entry_key, frozenset(kept), overflow=overflow)
        )
    return blocked
