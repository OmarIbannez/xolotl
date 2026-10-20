"""Reverse blocking over ``party_name_index`` (SP-03 spec §10.2, NFR-SC-006)."""

import json
import logging
from functools import cache
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, delete, insert, text
from sqlalchemy.orm import Session

from tests.helpers import new_tenant, staff_session
from tests.perf.conftest import SEED, draw_name, split_pools
from xolotl.modules.party import service
from xolotl.modules.party.enums import IdentifierType, PartyStatus, PartyType
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
    NameQuery,
    NormalizedName,
    blocking_plan,
    name_keys,
    normalize_name,
    phonetic_key,
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
BACKGROUND_PARTIES = 10_000
BACKGROUND_CHUNK = 2_000
# Four parties sharing a given name: more than a stop-key floor of three.
NAMESAKES = (
    "Ulises Beltrán Ochoa",
    "Ulises Cota Duarte",
    "Ulises Espino Fierro",
    "Ulises Galván Haro",
)


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


def _party_for(query: NameQuery) -> PartyCreateRequest:
    name = query.names[0]
    if query.entity_type is not EntityType.PERSON:
        return _organization(name)
    if not (query.given_names and query.first_surname):
        # No structure on the query: split the name. PersonInput requires a given
        # name and a first surname, so a lone token fills both.
        return _person(name)
    return PartyCreateRequest(
        party_type=PartyType.PERSON,
        person=PersonInput(
            given_names=query.given_names,
            first_surname=query.first_surname,
            second_surname=query.second_surname,
        ),
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


def _seed_background(s: Session, tenant_id: UUID, count: int) -> None:
    """Bulk insert a seeded Mexican base: the rows reverse blocking reads.

    Each party gets the ``party`` row its name row's foreign key needs and the
    ``party_name_index`` row ``index_party_names`` would write (the library's
    folded name, tokens and ``name_keys``), drawn as the perf base is: the party
    pool of ``split_pools`` with ``Draw(SEED)``. No person row, no events: the base
    is never screened, only blocked on.
    """
    rng = Draw(SEED)
    given, family = split_pools()[0]
    parties: list[dict[str, object]] = []
    rows: list[dict[str, object]] = []
    for _ in range(count):
        party_id = uuid7()
        given_names, first, second = draw_name(rng, given, family)
        name = normalize_name(
            f"{given_names} {first} {second}",
            entity_type=EntityType.PERSON,
            given=given_names,
            surname=f"{first} {second}",
        )
        parties.append({
            "tenant_id": tenant_id,
            "id": party_id,
            "party_type": PartyType.PERSON,
            "country_code": "MX",
        })
        rows.append({
            "tenant_id": tenant_id,
            "id": uuid7(),
            "party_id": party_id,
            "name_kind": NameKind.LEGAL,
            "norm_name": name.folded,
            "tokens": [t.folded for t in name.tokens],
            "phonetic_keys": sorted(name_keys(name)),
            "matcher_version": MATCHER_VERSION,
        })
    for at in range(0, count, BACKGROUND_CHUNK):
        _ = s.execute(insert(Party), parties[at : at + BACKGROUND_CHUNK])
        _ = s.execute(insert(PartyNameIndex), rows[at : at + BACKGROUND_CHUNK])


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
    # The surnames alone would block any Zamudio Quiroga, so keep only the key sets
    # that carry the nickname's phonetic key: the plan's nickname variants.
    nickname = BlockingPlan(
        tuple(keys for keys in plan.key_sets if phonetic_key("paco") in keys)
    )
    assert nickname.key_sets
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party_id = _add(s, _person("Paco Zamudio Quiroga"))
        control = _add(s, _person("Ramón Zamudio Quiroga"))
        by_nickname = set(s.scalars(blocking_query(nickname, "", limit=10)))
        found = _blocked(s, entry)
    assert by_nickname == {party_id}
    assert party_id in found.party_ids
    assert control in found.party_ids  # by the shared surnames, as designed


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


def _entry_names(entry: ListEntry) -> list[NormalizedName]:
    names = [
        normalize_name(
            name.full,
            entity_type=entry.entity_type,
            given=name.given,
            surname=name.surname,
        )
        for name in (entry.primary_name, *entry.aliases)
    ]
    return [name for name in names if name.tokens]


def _covering_key_sets(entry: ListEntry, keys: frozenset[str]) -> list[tuple[str, ...]]:
    return [
        key_set
        for name in _entry_names(entry)
        for key_set in blocking_plan(name, _idf()).key_sets
        if set(key_set) <= keys
    ]


def test_a_common_unrelated_name_is_blocked_only_through_single_keys_never_by_name(
    engine: Engine, tenant: CreatedTenant
):
    negative = _corpus().hard_negatives[0]
    keys = name_keys(
        normalize_name(negative.names[0], entity_type=negative.entity_type)
    )
    covering = {e.entry_key: _covering_key_sets(e, keys) for e in _corpus().entries}
    related = {key for key, sets in covering.items() if sets}
    # The library's single-key sets (one-letter deletions, prefix and suffix keys of
    # rare entry tokens) meet some unrelated entries by design; no key set of two or
    # more tokens, which is what blocks a name as a name, covers the party.
    assert all(len(key_set) == 1 for sets in covering.values() for key_set in sets)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party_id = _add(s, _party_for(negative))
        found = reverse_candidates(s, _corpus().entries, idf=_idf())
        # Still inside the transaction that set the trigram threshold.
        by_trigram = [
            name.folded
            for entry in _corpus().entries
            for name in _entry_names(entry)
            if party_id
            in s.scalars(blocking_query(BlockingPlan(()), name.folded, limit=1))
        ]
    blocked_on = {b.entry_key for b in found if party_id in b.party_ids}
    assert blocked_on == related
    assert by_trigram == []


def test_the_trigram_threshold_is_set_for_the_transaction(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = _blocked(s, _entry("Ulises Quintanilla"))
        threshold = s.scalar(text("SHOW pg_trgm.similarity_threshold"))
    assert threshold == "0.4"


def test_more_than_the_cap_marks_the_entry_overflow(
    engine: Engine, tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(blocking, "BLOCKING_CAP", 3)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        for second in ("Nava", "Nieto", "Olmos", "Pardo"):
            _ = _add(s, _person(f"Ulises Quintanilla {second}"))
        found = _blocked(s, _entry("Ulises Quintanilla"))
    assert found == BlockedEntry("E-1", frozenset(), overflow=True)


def test_an_overflowing_entry_keeps_its_identifier_matches(
    engine: Engine, tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(blocking, "BLOCKING_CAP", 3)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        for second in ("Nava", "Nieto", "Olmos", "Pardo"):
            _ = _add(s, _person(f"Ulises Quintanilla {second}"))
        party_id = _add(s, _person("Carlos Sánchez Gil", rfc="SAGC800101AB8"))
        found = _blocked(s, _entry("Ulises Quintanilla", rfc="SAGC800101AB8"))
    assert found == BlockedEntry("E-1", frozenset({party_id}), overflow=True)


def _split(entry: ListEntry, idf: IdfTable) -> tuple[BlockingPlan, BlockingPlan]:
    """Split the entry's plan into its single-key sets and its pair sets.

    Returns:
        ``(singles, pairs)``.

    """
    (name,) = _entry_names(entry)
    plan = blocking_plan(name, idf)
    singles = tuple(keys for keys in plan.key_sets if len(keys) == 1)
    pairs = tuple(keys for keys in plan.key_sets if len(keys) > 1)
    return BlockingPlan(singles), BlockingPlan(pairs)


def test_a_single_key_held_by_more_than_the_threshold_is_dropped(
    engine: Engine,
    tenant: CreatedTenant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setattr(blocking, "STOP_KEY_MIN_HOLDERS", 3)
    caplog.set_level(logging.DEBUG, logger=blocking.__name__)
    # Ulises is rare, so it blocks alone through its single keys; the common
    # surnames block as a pair.
    idf = IdfTable({"quintanilla": 0.3, "nava": 0.3})
    entry = _entry(
        "Ulises Quintanilla Nava", given="Ulises", surname="Quintanilla Nava"
    )
    singles, pairs = _split(entry, idf)
    assert singles.key_sets
    assert pairs.key_sets
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party_id = _add(s, _person("Ulises Quintanilla Nava"))
        namesakes = {_add(s, _person(name)) for name in NAMESAKES}
        (found,) = reverse_candidates(s, [entry], idf=idf)
        by_single = set(s.scalars(blocking_query(singles, "", limit=10)))
    # The given name's keys alone meet every Ulises: five rows, over the floor.
    assert by_single == namesakes | {party_id}
    # Dropped, so the party is found through the surname pair and no one else is.
    assert found == BlockedEntry("E-1", frozenset({party_id}), overflow=False)
    (record,) = [r for r in caplog.records if r.name == blocking.__name__]
    assert record.levelno == logging.DEBUG
    assert record.getMessage() == "screening.blocking_stop_keys"
    assert record.__dict__["dropped"] == len(singles.key_sets)
    assert "ulises" not in json.dumps(record.__dict__, default=str).lower()


def test_a_party_whose_single_keys_are_all_dropped_is_still_found_by_trigram(
    engine: Engine, tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(blocking, "STOP_KEY_MIN_HOLDERS", 3)
    # One rare token and one common one: single-key sets only, no pair.
    idf = IdfTable({"quintanilla": 0.3})
    entry = _entry("Ulises Quintanilla", given="Ulises", surname="Quintanilla")
    singles, pairs = _split(entry, idf)
    assert singles.key_sets
    assert not pairs.key_sets
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party_id = _add(s, _person("Ulises Quintanilla"))
        namesakes = {_add(s, _person(name)) for name in NAMESAKES}
        (found,) = reverse_candidates(s, [entry], idf=idf)
        by_single = set(s.scalars(blocking_query(singles, "", limit=10)))
    assert by_single == namesakes | {party_id}
    assert found == BlockedEntry("E-1", frozenset({party_id}), overflow=False)


def test_the_stop_key_rule_never_crosses_tenants(
    engine: Engine, tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(blocking, "STOP_KEY_MIN_HOLDERS", 3)
    idf = IdfTable({"quintanilla": 0.3})
    entry = _entry("Ulises Quintanilla", given="Ulises", surname="Quintanilla")
    other = new_tenant("beta", "admin@beta.mx")
    with staff_session(other.tenant_id, other.admin_user_id) as s:
        for name in NAMESAKES:
            _ = _add(s, _person(name))
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        # Found through the given name's keys only: no surname in common, and the
        # folded names are too far apart for the trigram predicate.
        party_id = _add(s, _person("Ulises Beltrán Ochoa"))
        # More rows than the floor, so the holders are counted rather than skipped.
        for name in (
            "Fernanda Ochoa",
            "Rocío Cota",
            "Teresa Espino",
            "Graciela Montemayor",
        ):
            _ = _add(s, _person(name))
        (found,) = reverse_candidates(s, [entry], idf=idf)
    # Beta's four Ulises would push the key over the floor; they are not counted.
    assert found.party_ids == {party_id}


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


def test_an_rfc_on_a_party_that_is_not_active_is_not_returned(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party_id = _add(s, _person("Carlos Sánchez Gil", rfc="SAGC800101AB8"))
        # No party operation merges yet; set the status the way a merge would,
        # leaving the identifier row in place.
        service.get_party(s, party_id).status = PartyStatus.MERGED
        s.flush()
        found = _blocked(
            s,
            _entry(
                "Comercializadora Tlaloc",
                entity_type=EntityType.ORGANIZATION,
                rfc="SAGC800101AB8",
            ),
        )
    assert found.party_ids == frozenset()


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
        # A Mexican base behind the sample: its common keys are the stop keys.
        _seed_background(s, tenant.tenant_id, BACKGROUND_PARTIES)
        for i, pair in enumerate(sample):
            party_of[i] = _add(s, _party_for(pair.query))
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
