"""Blocking plans and the in-memory candidate index (SP-03 spec §4.2, NFR-SC-006)."""

import logging
import statistics
import time
from collections.abc import Collection

import pytest

from tests.modules.screening.matching.support import entry, indexed
from xolotl.modules.screening.lists import EntityType, IdType
from xolotl.modules.screening.matching import (
    IdfTable,
    MemoryIndex,
    NameQuery,
    QueryIdentifier,
    ScoringConfig,
    blocking_plan,
    name_keys,
    normalize_name,
    phonetic_key,
    score,
    token_keys,
)

PERSON, ORG = EntityType.PERSON, EntityType.ORGANIZATION
ENTRIES = [
    entry(
        "1", "Joaquín Guzmán Loera", identifiers=((IdType.CURP, "GULJ570404HSLZRQ02"),)
    ),
    entry("2", "José Hernández López"),
    entry("3", "Juan Hernández García"),
    entry("4", "Acme Sol SA de CV", entity_type=ORG),
    entry("5", "Pepe Ntaganda"),
    entry("6", "Rosa Delisted", screenable=False),
    entry("7", "Guzman Holdings", entity_type=ORG, list_code="OFAC_SDN"),
    entry("8", "Ali Guzman", entity_type=EntityType.OTHER),
]
IDF = IdfTable.build(e for e in ENTRIES)
INDEX = MemoryIndex.build((indexed(e, n) for n, e in enumerate(ENTRIES)), idf=IDF)
ALL = frozenset({"UN_CONSOLIDATED", "OFAC_SDN"})


def _keys(query: NameQuery, shards: Collection[str] = ALL, top_k: int = 10):
    return [
        (c.indexed.entry.entry_key, c.via)
        for c in INDEX.candidates(query, shards=shards, top_k=top_k)
    ]


def test_non_screenable_entries_are_not_indexed():
    assert len(INDEX) == 7
    found = _keys(NameQuery(PERSON, ("Rosa Delisted",)))
    assert ("6", "NAME") not in found


def test_rare_tokens_block_alone_and_common_tokens_in_pairs():
    name = normalize_name("Bosco Ntaganda Hernández López", entity_type=PERSON)
    plan = blocking_plan(name, IDF)
    assert (phonetic_key("ntaganda"),) in plan.key_sets
    hernandez, lopez = phonetic_key("hernandez"), phonetic_key("lopez")
    assert tuple(sorted({hernandez, lopez})) in plan.key_sets
    assert (hernandez,) not in plan.key_sets


def test_a_single_token_name_blocks_on_itself():
    token = normalize_name("Hernández", entity_type=PERSON).tokens[0]
    plan = blocking_plan(normalize_name("Hernández", entity_type=PERSON), IDF)
    assert plan.key_sets == tuple(sorted((key,) for key in token_keys(token)))
    assert {"arnds", "ards", "p:her", "s:dez"} <= {k for (k,) in plan.key_sets}


def test_nickname_variants_are_alternatives():
    # "José" finds the entry listed as "Pepe".
    assert ("5", "NAME") in _keys(NameQuery(PERSON, ("José Ntaganda",)))
    assert ("2", "NAME") in _keys(NameQuery(PERSON, ("Pepe Hernández López",)))


def test_identifier_hits_come_first_and_beyond_top_k():
    query = NameQuery(
        PERSON,
        ("Ana Ruiz",),
        identifiers=(QueryIdentifier(IdType.CURP, "GULJ570404HSLZRQ02"),),
    )
    assert _keys(query, top_k=0) == [("1", "IDENTIFIER")]
    assert _keys(query, shards={"OFAC_SDN"}) == []
    both = NameQuery(PERSON, ("Joaquin Guzman",), identifiers=query.identifiers)
    found = _keys(both)
    assert found[0] == ("1", "IDENTIFIER")
    assert ("8", "NAME") in found
    assert ("1", "NAME") not in found


def test_shards_and_entity_types_filter_candidates():
    assert _keys(NameQuery(ORG, ("Guzman Holdings",))) == [("7", "NAME"), ("8", "NAME")]
    assert _keys(NameQuery(ORG, ("Guzman Holdings",)), shards={"UN_CONSOLIDATED"}) == [
        ("8", "NAME")
    ]
    assert ("1", "NAME") in _keys(NameQuery(PERSON, ("Joaquin Guzman",)))
    assert ("7", "NAME") not in _keys(NameQuery(PERSON, ("Joaquin Guzman",)))


def test_ranking_prefers_more_shared_weight():
    keys = _keys(NameQuery(PERSON, ("Juan Hernández García",)))
    assert keys[0] == ("3", "NAME")
    assert _keys(NameQuery(PERSON, ("Juan Hernández García",)), top_k=1) == [
        ("3", "NAME")
    ]


@pytest.mark.perf
def test_latency_at_50000_entries():
    # One name screened against 50,000 entries: retrieval plus scoring of the
    # candidates (SP-03 §8.3 budget: well under 300 ms at p95). Records, not gates.
    given = ["juan", "jose", "maria", "ana", "luis", "carlos", "rosa", "pedro"]
    entries = [
        entry(str(i), f"{given[i % 8]} Name{i} Surname{i // 7}") for i in range(50_000)
    ]
    idf = IdfTable.build(entries)
    index = MemoryIndex.build((indexed(e, n) for n, e in enumerate(entries)), idf=idf)
    config = ScoringConfig()
    timings = []
    for i in range(200):
        query = NameQuery(PERSON, (f"{given[i % 8]} Name{i * 97} Surname{i * 13}",))
        start = time.perf_counter()
        found = index.candidates(query, shards={"UN_CONSOLIDATED"}, top_k=50)
        for candidate in found:
            _ = score(query, candidate.indexed.entry, idf=idf, config=config)
        timings.append(time.perf_counter() - start)
    p95 = statistics.quantiles(timings, n=20)[-1]
    logging.getLogger(__name__).warning(
        "screen p95 over 50,000 entries: %.1f ms", p95 * 1000
    )
    assert p95 < 0.3, f"p95 {p95 * 1000:.1f} ms"


def test_keys_held_by_thousands_of_entries_do_not_retrieve_alone():
    crowd = [entry(str(i), f"Mohammedx{i:04d}") for i in range(1_200)]
    target = entry("t", "Zulkarnain Mohammedx")
    entries = [*crowd, target]
    idf = IdfTable.build(entries)
    index = MemoryIndex.build((indexed(e, n) for n, e in enumerate(entries)), idf=idf)
    found = index.candidates(
        NameQuery(PERSON, ("Zulkarnain Mohammedx",)),
        shards={"UN_CONSOLIDATED"},
        top_k=5,
    )
    # "Mohammedx" keys are held by 1,201 entries: they do not retrieve on their own,
    # so only the entry sharing the rare token comes back.
    assert [c.indexed.entry.entry_key for c in found] == ["t"]


def test_name_keys_cover_phonetic_deletion_and_affix_keys():
    name = normalize_name("Ntaganda", entity_type=PERSON)
    keys = name_keys(name)
    assert {"ntgnd", "tgnd", "ntgd", "p:nta", "s:nda"} <= keys
    short = normalize_name("Ho", entity_type=PERSON)
    assert name_keys(short) == {phonetic_key("ho")}
