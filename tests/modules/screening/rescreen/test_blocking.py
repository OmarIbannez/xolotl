"""Reverse blocking over ``party_name_index`` (SP-03 spec §10.2, NFR-SC-006)."""

import json
import logging
from functools import cache
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from pydantic import JsonValue
from sqlalchemy import Engine, Select, delete, text
from sqlalchemy.orm import Session

from tests.helpers import new_tenant, staff_session
from xolotl.modules.party import service
from xolotl.modules.party.enums import IdentifierType, PartyType
from xolotl.modules.party.models import Party
from xolotl.modules.party.retention import purge_party
from xolotl.modules.party.schemas import (
    IdentifierInput,
    OrganizationInput,
    PartyCreateRequest,
    PersonInput,
)
from xolotl.modules.screening.engine.enums import NameKind
from xolotl.modules.screening.engine.models import PartyNameIndex
from xolotl.modules.screening.engine.names import index_party_names
from xolotl.modules.screening.lists import (
    EntityType,
    EntryIdentifier,
    EntryName,
    IdType,
    ListEntry,
    NameQuality,
)
from xolotl.modules.screening.matching import (
    MATCHER_VERSION,
    BlockingPlan,
    IdfTable,
    blocking_plan,
    normalize_name,
)
from xolotl.modules.screening.matching.evaluation import (
    Corpus,
    CorpusPair,
    Draw,
    Floors,
    load_corpus,
    load_floors,
)
from xolotl.modules.screening.rescreen import blocking
from xolotl.modules.screening.rescreen.blocking import (
    BlockedEntry,
    blocking_query,
    reverse_candidates,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.ids import uuid7

SCREENING_TESTS = Path(__file__).parents[1]
RECALL_SAMPLE = 2000
SYNTHETIC_ROWS = 2000


@cache
def _floors() -> Floors:
    return load_floors(SCREENING_TESTS / "corpus" / "floors.yaml")


@cache
def _corpus() -> Corpus:
    return load_corpus(SCREENING_TESTS, _floors())


@cache
def _idf() -> IdfTable:
    return IdfTable.build(_corpus().entries)


def _person(name: str, *, rfc: str | None = None) -> PartyCreateRequest:
    given, _, surnames = name.partition(" ")
    first, _, second = surnames.partition(" ")
    return PartyCreateRequest(
        party_type=PartyType.PERSON,
        person=PersonInput(
            given_names=given,
            first_surname=first or given,
            second_surname=second or None,
        ),
        identifiers=[IdentifierInput(id_type=IdentifierType.RFC, value=rfc)]
        if rfc
        else [],
    )


def _organization(name: str) -> PartyCreateRequest:
    return PartyCreateRequest(
        party_type=PartyType.ORGANIZATION,
        organization=OrganizationInput(legal_name=name[:300]),
    )


def _add(s: Session, body: PartyCreateRequest) -> UUID:
    party = service.create_party(s, body)
    _ = index_party_names(s, party.id)
    return party.id


def _entry(
    full: str,
    *,
    key: str = "E-1",
    entity_type: EntityType = EntityType.PERSON,
    given: str | None = None,
    surname: str | None = None,
    rfc: str | None = None,
    aliases: tuple[EntryName, ...] = (),
) -> ListEntry:
    identifiers = (
        (EntryIdentifier(id_type=IdType.RFC, value=rfc, normalized=rfc),) if rfc else ()
    )
    return ListEntry(
        list_code="UN_CONSOLIDATED",
        entry_key=key,
        entity_type=entity_type,
        primary_name=EntryName(
            full=full, quality=NameQuality.PRIMARY, given=given, surname=surname
        ),
        aliases=aliases,
        identifiers=identifiers,
    )


def _blocked(s: Session, entry: ListEntry) -> BlockedEntry:
    (found,) = reverse_candidates(s, [entry], idf=_idf())
    return found


def test_a_dropped_second_surname_and_an_accent_loss_are_blocked(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party_id = _add(s, _person("Jose Martinez"))
        other = _add(s, _person("Fernanda Ochoa Beltrán"))
        found = _blocked(
            s,
            _entry(
                "José Martínez Hernández",
                given="José",
                surname="Martínez Hernández",
            ),
        )
    assert found == BlockedEntry("E-1", frozenset({party_id}), overflow=False)
    assert other not in found.party_ids


def test_a_nickname_variant_is_blocked_through_the_phonetic_keys(
    engine: Engine, tenant: CreatedTenant
):
    entry = _entry("Francisco Zamudio Quiroga", given="Francisco")
    plan = blocking_plan(
        normalize_name(entry.primary_name.full, entity_type=EntityType.PERSON),
        _idf(),
    )
    paco = normalize_name("Paco Zamudio Quiroga", entity_type=EntityType.PERSON)
    paco_keys = {t.phonetic for t in paco.tokens}
    # The library's key sets cover the nickname: one of them is all Paco's keys.
    assert any(set(keys) <= paco_keys for keys in plan.key_sets)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party_id = _add(s, _person("Paco Zamudio Quiroga"))
        query = blocking_query(plan, "", limit=10)
        by_keys = set(s.scalars(query))
        found = _blocked(s, entry)
    assert by_keys == {party_id}
    assert found.party_ids == {party_id}


def test_an_rfc_on_the_entry_finds_the_party_without_a_name_match(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party_id = _add(s, _person("Carlos Sánchez Gil", rfc="SAGC800101AB8"))
        found = _blocked(
            s,
            _entry(
                "Comercializadora Tlaloc",
                entity_type=EntityType.ORGANIZATION,
                rfc="SAGC800101AB8",
            ),
        )
    assert found.party_ids == {party_id}


def test_an_invalid_list_rfc_falls_back_to_names(
    engine: Engine, tenant: CreatedTenant, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG, logger=blocking.__name__)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party_id = _add(s, _person("Rogelio Villaseñor Ibarra"))
        found = _blocked(s, _entry("Rogelio Villaseñor Ibarra", rfc="NOT-AN-RFC"))
    assert found.party_ids == {party_id}
    (record,) = [r for r in caplog.records if r.name == blocking.__name__]
    assert record.levelno == logging.DEBUG
    assert record.getMessage() == "screening.blocking_identifier_invalid"
    assert "Rogelio" not in json.dumps(record.__dict__, default=str)


def test_a_common_unrelated_name_is_not_blocked(engine: Engine, tenant: CreatedTenant):
    negative = _corpus().hard_negatives[0]
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party_id = _add(s, _person(negative.names[0]))
        found = reverse_candidates(s, _corpus().entries, idf=_idf())
    blocked_on = [b.entry_key for b in found if party_id in b.party_ids]
    assert blocked_on == []


def test_more_than_the_cap_marks_the_entry_overflow(
    engine: Engine, tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(blocking, "BLOCKING_CAP", 3)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        for second in ("Nava", "Nieto", "Olmos", "Pardo"):
            _ = _add(s, _person(f"Ulises Quintanilla {second}"))
        found = _blocked(s, _entry("Ulises Quintanilla"))
    assert found == BlockedEntry("E-1", frozenset(), overflow=True)


def test_blocking_never_crosses_tenants(engine: Engine, tenant: CreatedTenant):
    other = new_tenant("beta", "admin@beta.mx")
    with staff_session(other.tenant_id, other.admin_user_id) as s:
        _ = _add(s, _person("Graciela Montemayor Ruvalcaba", rfc="SAGC800101AB8"))
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        found = _blocked(
            s, _entry("Graciela Montemayor Ruvalcaba", rfc="SAGC800101AB8")
        )
    assert found.party_ids == frozenset()


def test_purged_parties_are_not_returned(engine: Engine, tenant: CreatedTenant):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party_id = _add(s, _person("Leopoldo Arizmendi Cota", rfc="SAGC800101AB8"))
        _ = purge_party(s, service.get_party(s, party_id))
        # F5's purge subscriber deletes the rows; simulate it here.
        _ = s.execute(delete(PartyNameIndex).where(PartyNameIndex.party_id == party_id))
        found = _blocked(s, _entry("Leopoldo Arizmendi Cota", rfc="SAGC800101AB8"))
    assert found.party_ids == frozenset()


def _plan_nodes(node: JsonValue) -> list[dict[str, JsonValue]]:
    if isinstance(node, list):
        return [n for child in node for n in _plan_nodes(child)]
    if isinstance(node, dict):
        return [node, *(n for child in node.values() for n in _plan_nodes(child))]
    return []


def _explain(s: Session, query: Select[UUID]) -> tuple[set[str], set[str]]:
    compiled = query.compile(dialect=s.get_bind().dialect)
    rows = s.connection().exec_driver_sql(
        f"EXPLAIN (FORMAT JSON) {compiled}", compiled.params
    )
    raw = cast("JsonValue", rows.scalar_one())
    nodes = _plan_nodes(raw)
    indexes = {str(n["Index Name"]) for n in nodes if "Index Name" in n}
    scans = {
        str(n.get("Relation Name")) for n in nodes if n.get("Node Type") == "Seq Scan"
    }
    return indexes, scans


def test_the_blocking_query_uses_the_gin_indexes(
    engine: Engine, admin_engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        parties = [
            Party(
                tenant_id=tenant.tenant_id,
                id=uuid7(),
                party_type=PartyType.PERSON,
                country_code="MX",
            )
            for _ in range(SYNTHETIC_ROWS)
        ]
        s.add_all(parties)
        s.flush()
        for n, party in enumerate(parties):
            name = normalize_name(
                f"Persona{n:04d} Apellido{n % 97:02d} Segundo{n % 89:02d}",
                entity_type=EntityType.PERSON,
            )
            s.add(
                PartyNameIndex(
                    tenant_id=tenant.tenant_id,
                    id=uuid7(),
                    party_id=party.id,
                    name_kind=NameKind.LEGAL,
                    norm_name=name.folded,
                    tokens=[t.folded for t in name.tokens],
                    phonetic_keys=sorted({t.phonetic for t in name.tokens}),
                    matcher_version=MATCHER_VERSION,
                )
            )
    # VACUUM also flushes the GIN pending lists, as autovacuum would.
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        _ = conn.execute(text("VACUUM ANALYZE party_name_index"))
    probe = normalize_name(
        "Persona0042 Apellido42 Segundo42", entity_type=EntityType.PERSON
    )
    keys = (probe.tokens[0].phonetic,)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = s.execute(
            text("SELECT set_config('pg_trgm.similarity_threshold', '0.4', true)")
        )
        by_keys = blocking_query(BlockingPlan(key_sets=(keys,)), "", limit=10)
        by_trigram = blocking_query(BlockingPlan(key_sets=()), probe.folded, limit=10)
        phonetic_indexes, phonetic_scans = _explain(s, by_keys)
        trigram_indexes, trigram_scans = _explain(s, by_trigram)
    assert "ix_pni_phonetic" in phonetic_indexes
    assert "ix_pni_trgm" in trigram_indexes
    assert "party_name_index" not in phonetic_scans | trigram_scans


def _sample() -> list[CorpusPair]:
    pairs = list(_corpus().pairs)
    return Draw(_floors().seed).sample(pairs, min(RECALL_SAMPLE, len(pairs)))


def test_postgres_blocking_retrieval_recall_meets_the_floor(
    engine: Engine, tenant: CreatedTenant
):
    corpus = _corpus()
    sample = _sample()
    floor = min(kind["retrieval_recall"] for kind in _floors().floors.values())
    party_of: dict[int, UUID] = {}
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        for i, pair in enumerate(sample):
            name = pair.query.names[0]
            body = (
                _person(name)
                if pair.query.entity_type is EntityType.PERSON
                else _organization(name)
            )
            party_of[i] = _add(s, body)
        entries = list(
            {
                (e.list_code, e.entry_key): e for e in map(corpus.entry_for, sample)
            }.values()
        )
        blocked = reverse_candidates(s, entries, idf=_idf())
    found = {
        (e.list_code, e.entry_key): b.party_ids
        for e, b in zip(entries, blocked, strict=True)
    }
    missed = [
        pair
        for i, pair in enumerate(sample)
        if party_of[i] not in found[pair.list_code, pair.entry_key]
    ]
    recall = 1 - len(missed) / len(sample)
    names = [
        f"{p.list_code}:{p.entry_key} "
        + normalize_name(p.query.names[0], entity_type=p.query.entity_type).folded
        for p in missed
    ]
    assert recall >= floor, f"recall {recall:.4f}, missed pairs:\n" + "\n".join(names)
