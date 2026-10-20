"""Candidate retrieval: blocking plans and the in-memory index (SP-03 spec §4.2).

Blocking keeps one query from being scored against every entry. A name's weighted
tokens give phonetic keys (with the keys of their nickname variants as
alternatives); a rare token is enough on its own, common tokens must come in pairs.
An entry is a candidate when its keys contain every key of any one set. The same
plan drives the Postgres reverse search (§10.2), where the keys are a GIN-indexed
array.

``MemoryIndex`` holds the shared lists in each process (Decision 2: no search
service). Exact identifier hits always come back, ahead of and beyond ``top_k``;
name candidates are ranked by the IDF weight of the query tokens they share.
Non-screenable entries (69-B DESVIRTUADO, SENTENCIA_FAVORABLE) are not indexed.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from itertools import combinations, product
from typing import Literal
from uuid import UUID

from xolotl.modules.screening.lists import IdType, ListEntry
from xolotl.modules.screening.matching.idf import WEIGHTED_ROLES, IdfTable
from xolotl.modules.screening.matching.names import entry_names
from xolotl.modules.screening.matching.nicknames import nickname_variants
from xolotl.modules.screening.matching.normalize import NormalizedName, Token
from xolotl.modules.screening.matching.phonetic import phonetic_key
from xolotl.modules.screening.matching.query import NameQuery, compatible, query_names

RARE_WEIGHT = 0.6
STOP_KEY_POSTINGS = 1_000
_AFFIX = 3
_AFFIX_MIN = 4
_DELETION_MIN = 3
_MAX_SETS = 128


@dataclass(frozen=True, slots=True)
class BlockingPlan:
    key_sets: tuple[tuple[str, ...], ...]


def _deletions(key: str) -> set[str]:
    if len(key) < _DELETION_MIN or not key.isascii():
        return set()
    return {key[:i] + key[i + 1 :] for i in range(len(key))}


def token_keys(token: Token) -> frozenset[str]:
    """List the blocking keys a name token is filed under.

    The phonetic key; its one-letter deletions (so any single-letter edit of a rare
    token still meets the entry: both sides hold the deletions, as in SymSpell);
    and a three-letter prefix and suffix key for ASCII tokens of four letters or
    more, so a typo that rewrites the phonetic key (Prelasa against Prelala) still
    meets through the untouched end of the word.

    Returns:
        The keys; ``party_name_index.phonetic_keys`` stores the same set.

    """
    keys = {token.phonetic} | _deletions(token.phonetic)
    folded = token.folded
    if folded.isascii() and len(folded) >= _AFFIX_MIN:
        keys.update((f"p:{folded[:_AFFIX]}", f"s:{folded[-_AFFIX:]}"))
    return frozenset(keys)


def name_keys(name: NormalizedName) -> frozenset[str]:
    """Collect the blocking keys of every token of a name.

    Returns:
        The union of ``token_keys`` over the name's tokens.

    """
    return frozenset(key for token in name.tokens for key in token_keys(token))


def _alternatives(token: Token, *, loose: bool) -> tuple[str, ...]:
    # Rare tokens block alone, so they may use every key; pairs of common tokens use
    # the phonetic key and nickname variants only, or the pairs would multiply.
    keys = set(token_keys(token)) if loose else {token.phonetic}
    keys |= {phonetic_key(v) for v in nickname_variants(token.folded)}
    return tuple(sorted(keys))


def blocking_plan(name: NormalizedName, idf: IdfTable) -> BlockingPlan:
    """Plan the key sets that retrieve candidates for one name.

    Returns:
        Sorted key sets: one per rare token (and per nickname variant), one per pair
        of common tokens; a single token alone when the name has only one.

    """
    tokens = [t for t in name.tokens if t.role in WEIGHTED_ROLES] or list(name.tokens)
    sets: set[tuple[str, ...]] = set()
    if len(tokens) == 1:
        sets.update((key,) for key in _alternatives(tokens[0], loose=True))
    rare = [t for t in tokens if idf.weight(t.folded) >= RARE_WEIGHT]
    common = [t for t in tokens if idf.weight(t.folded) < RARE_WEIGHT]
    for token in rare:
        sets.update((key,) for key in _alternatives(token, loose=True))
    for first, second in combinations(common, 2):
        pairs = product(
            _alternatives(first, loose=False), _alternatives(second, loose=False)
        )
        sets.update(tuple(sorted(set(keys))) for keys in pairs)
    return BlockingPlan(key_sets=tuple(sorted(sets))[:_MAX_SETS])


@dataclass(frozen=True, slots=True)
class IndexedEntry:
    entry: ListEntry
    ref: UUID
    shard: str


@dataclass(frozen=True, slots=True)
class Candidate:
    indexed: IndexedEntry
    via: Literal["IDENTIFIER", "NAME"]


def _entry_keys(entry: ListEntry) -> frozenset[str]:
    return frozenset(
        key for name, _quality in entry_names(entry) for key in name_keys(name)
    )


class MemoryIndex:
    """An immutable index over screenable entries; rebuild it for a new version."""

    def __init__(
        self,
        entries: tuple[IndexedEntry, ...],
        keys: tuple[frozenset[str], ...],
        postings: dict[str, frozenset[int]],
        identifiers: dict[tuple[IdType, str], tuple[int, ...]],
        idf: IdfTable,
    ) -> None:
        self._entries: tuple[IndexedEntry, ...] = entries
        self._keys: tuple[frozenset[str], ...] = keys
        self._postings: dict[str, frozenset[int]] = postings
        self._identifiers: dict[tuple[IdType, str], tuple[int, ...]] = identifiers
        self._idf: IdfTable = idf

    @classmethod
    def build(cls, entries: Iterable[IndexedEntry], *, idf: IdfTable) -> "MemoryIndex":
        """Index the screenable entries.

        Returns:
            The index.

        """
        kept = tuple(e for e in entries if e.entry.screenable)
        keys = tuple(_entry_keys(e.entry) for e in kept)
        postings: dict[str, set[int]] = {}
        identifiers: dict[tuple[IdType, str], list[int]] = {}
        for position, (indexed, entry_keys) in enumerate(zip(kept, keys, strict=True)):
            for key in entry_keys:
                postings.setdefault(key, set()).add(position)
            for identifier in indexed.entry.identifiers:
                slot = (identifier.id_type, identifier.normalized)
                identifiers.setdefault(slot, []).append(position)
        return cls(
            kept,
            keys,
            {k: frozenset(v) for k, v in postings.items()},
            {k: tuple(v) for k, v in identifiers.items()},
            idf,
        )

    def __len__(self) -> int:
        """Count the indexed entries.

        Returns:
            The number of screenable entries held.

        """
        return len(self._entries)

    def candidates(
        self, query: NameQuery, *, shards: Collection[str], top_k: int
    ) -> list[Candidate]:
        """Retrieve the entries worth scoring for a query.

        Returns:
            Identifier hits first (all of them), then up to ``top_k`` name
            candidates by shared IDF weight, ties in index order.

        """
        found: list[Candidate] = []
        seen: set[int] = set()
        for identifier in query.identifiers:
            for position in self._identifiers.get(
                (identifier.id_type, identifier.normalized), ()
            ):
                if position not in seen and self._entries[position].shard in shards:
                    seen.add(position)
                    found.append(Candidate(self._entries[position], "IDENTIFIER"))
        ranked = self._name_candidates(query, shards, seen)
        found.extend(Candidate(self._entries[p], "NAME") for p in ranked[:top_k])
        return found

    def _name_candidates(
        self, query: NameQuery, shards: Collection[str], seen: set[int]
    ) -> list[int]:
        overlap: dict[int, float] = {}
        for name in query_names(query):
            plan = blocking_plan(name, self._idf)
            weighted = [t for t in name.tokens if t.role in WEIGHTED_ROLES]
            hits: set[int] = set()
            for key_set in plan.key_sets:
                first, *rest = (self._postings.get(k, frozenset()) for k in key_set)
                if not rest and len(first) > STOP_KEY_POSTINGS:
                    # A single key held by thousands of entries ("p:moh") names
                    # no one; its token still counts through the other keys.
                    continue
                hits.update(first.intersection(*rest))
            for position in hits:
                indexed = self._entries[position]
                if position in seen or indexed.shard not in shards:
                    continue
                if not compatible(query.entity_type, indexed.entry.entity_type):
                    continue
                shared = sum(
                    self._idf.weight(t.folded)
                    for t in weighted
                    if not self._keys[position].isdisjoint(
                        _alternatives(t, loose=False)
                    )
                )
                overlap[position] = max(overlap.get(position, 0.0), shared)
        return sorted(overlap, key=lambda p: (-overlap[p], p))
