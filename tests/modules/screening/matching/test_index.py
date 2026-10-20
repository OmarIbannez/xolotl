"""Blocking plans and the in-memory candidate index (SP-03 spec §4.2, NFR-SC-006)."""

import logging
import os
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
    blocking_plan,
    normalize_name,
    phonetic_key,
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
    assert _keys(NameQuery(PERSON, ("Rosa Delisted",))) == []


def test_rare_tokens_block_alone_and_common_tokens_in_pairs():
    name = normalize_name("Bosco Ntaganda Hernández López", entity_type=PERSON)
    plan = blocking_plan(name, IDF)
    assert (phonetic_key("ntaganda"),) in plan.key_sets
    hernandez, lopez = phonetic_key("hernandez"), phonetic_key("lopez")
    assert tuple(sorted({hernandez, lopez})) in plan.key_sets
    assert (hernandez,) not in plan.key_sets


def test_a_single_token_name_blocks_on_itself():
    plan = blocking_plan(normalize_name("Hernández", entity_type=PERSON), IDF)
    assert plan.key_sets == ((phonetic_key("hernandez"),),)


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
    assert _keys(both) == [("1", "IDENTIFIER"), ("8", "NAME")]


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


@pytest.mark.skipif(
    os.environ.get("XOLOTL_PERF") != "1",
    reason="non-gating latency record (SP-03 §8.3); run with XOLOTL_PERF=1",
)
def test_latency_at_50000_entries():
    given = ["juan", "jose", "maria", "ana", "luis", "carlos", "rosa", "pedro"]
    entries = [
        entry(str(i), f"{given[i % 8]} Name{i} Surname{i // 7}") for i in range(50_000)
    ]
    idf = IdfTable.build(entries)
    index = MemoryIndex.build((indexed(e, n) for n, e in enumerate(entries)), idf=idf)
    timings = []
    for i in range(200):
        query = NameQuery(PERSON, (f"{given[i % 8]} Name{i * 97} Surname{i * 13}",))
        start = time.perf_counter()
        _ = index.candidates(query, shards={"UN_CONSOLIDATED"}, top_k=50)
        timings.append(time.perf_counter() - start)
    p95 = statistics.quantiles(timings, n=20)[-1]
    logging.getLogger(__name__).warning(
        "candidates p95 over 50,000 entries: %.1f ms", p95 * 1000
    )
    assert p95 < 0.3, f"p95 {p95 * 1000:.1f} ms"
